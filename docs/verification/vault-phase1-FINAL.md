# AIEM Verification Vault — Phase 1: Database Schema
# Permanent Record

**Applied:** 2026-07-25  
**Scope:** AIEM components only. Options Engine and StockScanner AI excluded.  
**git HEAD at time of application:** `62a1077b9afed8746ea290daa63baae27b8bbb4c`  
**git tree state:** DIRTY (one untracked file: `attached_assets/Pasted--Directive-AEIM-Vault-Phase-1-...`)

---

## Phase 1 Checklist Status

| Item | Status |
|---|---|
| sha256 cross-check of `verified_run.sh` | `2617d7bb...` — PENDING Joel's independent confirmation (Section 21.4 vault-phase0-FINAL.md) |
| sha256 cross-check of `tools/verify_chain.sh` | `972ff44a...` — UNCONFIRMED (agent-memory only) |
| sha256 cross-check of `artifacts/stock-scanner-api/verify_chain.sh` | `ca7896c7...` — UNCONFIRMED (agent-memory only) |
| Pre-action scope statement | completed — see section 1 below |
| Full DDL applied | confirmed — raw SQL in section 2 |
| Raw proof tables created | confirmed — section 3 |
| Raw proof indexes created | confirmed — section 3 |
| Raw proof triggers created | confirmed — section 3 |
| Immutability rejection proof (UPDATE/DELETE) for each protected table | confirmed — section 4, all 10 tables |
| Permanent record committed | this file |

---

## 1. Scope Statement (pre-action gate)

Phase 1 creates 10 vault schema tables in the AIEM Postgres database. Design is append-only / event-sourced: no UPDATE or DELETE is permitted on any vault table once a row is written. Status progressions (export jobs, access grants) are represented as new rows, never mutations.

Existing tables checked before applying DDL:
- No `vault_*` tables existed
- Equivalent-purpose tables found: `aiem_verification_log`, `aiem_verification_logs`, and 26 other audit/snapshot tables — all operational AIEM tables with different schemas; no reuse appropriate

---

## 2. Full DDL Applied

**DDL file:** `docs/verification/vault-phase1-schema.sql`  
**sha256:** `e41d5f6a0a1cac1c9d04e603a592f48c4f509bae8b54e808b093716c8b6e6740`

```sql
-- AIEM Verification Vault — Phase 1 Schema
-- Scope: AIEM components only. Options Engine and StockScanner AI excluded.
-- Design: append-only / immutable. No UPDATE or DELETE is permitted on any vault table.
--         Status progressions (export jobs, access grants) are represented as new rows.
-- Applied: 2026-07-25
-- git HEAD at time of application: 62a1077b9afed8746ea290daa63baae27b8bbb4c

BEGIN;

-- Shared immutability trigger function
CREATE OR REPLACE FUNCTION vault_immutability_guard()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION
    'vault_immutability_violation: % on table "%" is permanently prohibited. '
    'The vault schema is append-only. Insert new rows to record state changes.',
    TG_OP, TG_TABLE_NAME;
END;
$$;

-- 1. vault_components
CREATE TABLE IF NOT EXISTS vault_components (
  id               BIGSERIAL   PRIMARY KEY,
  component_name   TEXT        NOT NULL,
  component_type   TEXT        NOT NULL,
  file_path        TEXT,
  sha256           TEXT,
  phase_discovered INTEGER     NOT NULL DEFAULT 0,
  metadata         JSONB       NOT NULL DEFAULT '{}',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_components_type  ON vault_components (component_type);
CREATE INDEX IF NOT EXISTS idx_vault_components_phase ON vault_components (phase_discovered);
CREATE TRIGGER vault_components_immutable
  BEFORE UPDATE OR DELETE ON vault_components
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 2. vault_component_relationships
CREATE TABLE IF NOT EXISTS vault_component_relationships (
  id                BIGSERIAL   PRIMARY KEY,
  from_component_id BIGINT      NOT NULL REFERENCES vault_components(id),
  to_component_id   BIGINT      NOT NULL REFERENCES vault_components(id),
  relationship_type TEXT        NOT NULL,
  metadata          JSONB       NOT NULL DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_rels_from ON vault_component_relationships (from_component_id);
CREATE INDEX IF NOT EXISTS idx_vault_rels_to   ON vault_component_relationships (to_component_id);
CREATE TRIGGER vault_component_relationships_immutable
  BEFORE UPDATE OR DELETE ON vault_component_relationships
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 3. vault_verification_runs
CREATE TABLE IF NOT EXISTS vault_verification_runs (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  run_type     TEXT        NOT NULL,
  operator_id  TEXT        NOT NULL DEFAULT 'system',
  status       TEXT        NOT NULL DEFAULT 'open',
  summary      JSONB       NOT NULL DEFAULT '{}',
  started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_runs_type   ON vault_verification_runs (run_type);
CREATE INDEX IF NOT EXISTS idx_vault_runs_status ON vault_verification_runs (status);
CREATE TRIGGER vault_verification_runs_immutable
  BEFORE UPDATE OR DELETE ON vault_verification_runs
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 4. vault_component_verifications
CREATE TABLE IF NOT EXISTS vault_component_verifications (
  id                BIGSERIAL   PRIMARY KEY,
  run_id            UUID        NOT NULL REFERENCES vault_verification_runs(id),
  component_id      BIGINT      REFERENCES vault_components(id),
  verification_type TEXT        NOT NULL,
  result            TEXT        NOT NULL,
  evidence_ref      TEXT,
  notes             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_cv_run       ON vault_component_verifications (run_id);
CREATE INDEX IF NOT EXISTS idx_vault_cv_component ON vault_component_verifications (component_id);
CREATE INDEX IF NOT EXISTS idx_vault_cv_result    ON vault_component_verifications (result);
CREATE TRIGGER vault_component_verifications_immutable
  BEFORE UPDATE OR DELETE ON vault_component_verifications
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 5. vault_evidence_artifacts
CREATE TABLE IF NOT EXISTS vault_evidence_artifacts (
  id            BIGSERIAL   PRIMARY KEY,
  run_id        UUID        REFERENCES vault_verification_runs(id),
  artifact_type TEXT        NOT NULL,
  label         TEXT        NOT NULL,
  file_path     TEXT,
  sha256        TEXT,
  content       TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_ea_run  ON vault_evidence_artifacts (run_id);
CREATE INDEX IF NOT EXISTS idx_vault_ea_type ON vault_evidence_artifacts (artifact_type);
CREATE TRIGGER vault_evidence_artifacts_immutable
  BEFORE UPDATE OR DELETE ON vault_evidence_artifacts
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 6. vault_source_snapshots
CREATE TABLE IF NOT EXISTS vault_source_snapshots (
  id           BIGSERIAL   PRIMARY KEY,
  component_id BIGINT      REFERENCES vault_components(id),
  git_commit   TEXT        NOT NULL,
  git_tree     TEXT        NOT NULL DEFAULT 'UNKNOWN',
  file_path    TEXT,
  sha256       TEXT,
  snapped_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_ss_component ON vault_source_snapshots (component_id);
CREATE INDEX IF NOT EXISTS idx_vault_ss_commit    ON vault_source_snapshots (git_commit);
CREATE TRIGGER vault_source_snapshots_immutable
  BEFORE UPDATE OR DELETE ON vault_source_snapshots
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 7. vault_deployment_snapshots
CREATE TABLE IF NOT EXISTS vault_deployment_snapshots (
  id            BIGSERIAL   PRIMARY KEY,
  snapshot_type TEXT        NOT NULL,
  git_commit    TEXT        NOT NULL,
  git_tree      TEXT        NOT NULL DEFAULT 'UNKNOWN',
  environment   TEXT        NOT NULL DEFAULT 'production',
  metadata      JSONB       NOT NULL DEFAULT '{}',
  snapped_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_ds_type ON vault_deployment_snapshots (snapshot_type);
CREATE INDEX IF NOT EXISTS idx_vault_ds_env  ON vault_deployment_snapshots (environment);
CREATE TRIGGER vault_deployment_snapshots_immutable
  BEFORE UPDATE OR DELETE ON vault_deployment_snapshots
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 8. vault_audit_events
CREATE TABLE IF NOT EXISTS vault_audit_events (
  id           BIGSERIAL   PRIMARY KEY,
  event_type   TEXT        NOT NULL,
  actor        TEXT        NOT NULL DEFAULT 'system',
  target_table TEXT,
  target_id    TEXT,
  payload      JSONB       NOT NULL DEFAULT '{}',
  event_ts     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_ae_type     ON vault_audit_events (event_type);
CREATE INDEX IF NOT EXISTS idx_vault_ae_actor    ON vault_audit_events (actor);
CREATE INDEX IF NOT EXISTS idx_vault_ae_event_ts ON vault_audit_events (event_ts);
CREATE TRIGGER vault_audit_events_immutable
  BEFORE UPDATE OR DELETE ON vault_audit_events
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 9. vault_access_grants
CREATE TABLE IF NOT EXISTS vault_access_grants (
  id           BIGSERIAL   PRIMARY KEY,
  grantee      TEXT        NOT NULL,
  access_type  TEXT        NOT NULL,
  grant_action TEXT        NOT NULL DEFAULT 'grant',
  granted_by   TEXT        NOT NULL DEFAULT 'system',
  expires_at   TIMESTAMPTZ,
  notes        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_ag_grantee ON vault_access_grants (grantee);
CREATE INDEX IF NOT EXISTS idx_vault_ag_action  ON vault_access_grants (grant_action);
CREATE TRIGGER vault_access_grants_immutable
  BEFORE UPDATE OR DELETE ON vault_access_grants
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

-- 10. vault_export_jobs
CREATE TABLE IF NOT EXISTS vault_export_jobs (
  id           BIGSERIAL   PRIMARY KEY,
  job_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
  requested_by TEXT        NOT NULL DEFAULT 'system',
  export_type  TEXT        NOT NULL,
  status       TEXT        NOT NULL DEFAULT 'pending',
  payload      JSONB       NOT NULL DEFAULT '{}',
  completed_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vault_ej_job_id ON vault_export_jobs (job_id);
CREATE INDEX IF NOT EXISTS idx_vault_ej_status ON vault_export_jobs (status);
CREATE TRIGGER vault_export_jobs_immutable
  BEFORE UPDATE OR DELETE ON vault_export_jobs
  FOR EACH ROW EXECUTE FUNCTION vault_immutability_guard();

COMMIT;
```

---

## 3. Raw Proof — Tables, Indexes, Triggers Created

### Tables created (raw query output)

```
vault_access_grants
vault_audit_events
vault_component_relationships
vault_component_verifications
vault_components
vault_deployment_snapshots
vault_evidence_artifacts
vault_export_jobs
vault_source_snapshots
vault_verification_runs
```

### Indexes created (raw query output)

```
idx_vault_ag_action
idx_vault_ag_grantee
vault_access_grants_pkey
idx_vault_ae_actor
idx_vault_ae_event_ts
idx_vault_ae_type
vault_audit_events_pkey
idx_vault_rels_from
idx_vault_rels_to
vault_component_relationships_pkey
idx_vault_cv_component
idx_vault_cv_result
idx_vault_cv_run
vault_component_verifications_pkey
idx_vault_components_phase
idx_vault_components_type
vault_components_pkey
idx_vault_ds_env
idx_vault_ds_type
vault_deployment_snapshots_pkey
idx_vault_ea_run
idx_vault_ea_type
vault_evidence_artifacts_pkey
idx_vault_ej_job_id
idx_vault_ej_status
vault_export_jobs_pkey
idx_vault_ss_commit
idx_vault_ss_component
vault_source_snapshots_pkey
idx_vault_runs_status
idx_vault_runs_type
vault_verification_runs_pkey
```

### Triggers created (raw query output)

```
vault_access_grants: vault_access_grants_immutable (DELETE)
vault_access_grants: vault_access_grants_immutable (UPDATE)
vault_audit_events: vault_audit_events_immutable (DELETE)
vault_audit_events: vault_audit_events_immutable (UPDATE)
vault_component_relationships: vault_component_relationships_immutable (DELETE)
vault_component_relationships: vault_component_relationships_immutable (UPDATE)
vault_component_verifications: vault_component_verifications_immutable (DELETE)
vault_component_verifications: vault_component_verifications_immutable (UPDATE)
vault_components: vault_components_immutable (DELETE)
vault_components: vault_components_immutable (UPDATE)
vault_deployment_snapshots: vault_deployment_snapshots_immutable (DELETE)
vault_deployment_snapshots: vault_deployment_snapshots_immutable (UPDATE)
vault_evidence_artifacts: vault_evidence_artifacts_immutable (DELETE)
vault_evidence_artifacts: vault_evidence_artifacts_immutable (UPDATE)
vault_export_jobs: vault_export_jobs_immutable (DELETE)
vault_export_jobs: vault_export_jobs_immutable (UPDATE)
vault_source_snapshots: vault_source_snapshots_immutable (DELETE)
vault_source_snapshots: vault_source_snapshots_immutable (UPDATE)
vault_verification_runs: vault_verification_runs_immutable (DELETE)
vault_verification_runs: vault_verification_runs_immutable (UPDATE)
```

### Trigger function body (raw from pg_proc)

```
BEGIN
  RAISE EXCEPTION
    'vault_immutability_violation: % on table "%" is permanently prohibited. '
    'The vault schema is append-only. Insert new rows to record state changes.',
    TG_OP, TG_TABLE_NAME;
END;
```

---

## 4. Raw Immutability Rejection Proof — All 10 Tables

For each table: one row INSERTed, then UPDATE attempted, then DELETE attempted. Raw error text from psycopg2 `pgerror` field pasted verbatim.

### vault_components
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_components" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_components" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_component_relationships
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_component_relationships" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_component_relationships" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_verification_runs
```
INSERT OK — id=43cf1b5a-a572-4e67-9440-a17a0838beef
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_verification_runs" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_verification_runs" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_component_verifications
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_component_verifications" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_component_verifications" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_evidence_artifacts
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_evidence_artifacts" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_evidence_artifacts" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_source_snapshots
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_source_snapshots" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_source_snapshots" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_deployment_snapshots
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_deployment_snapshots" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_deployment_snapshots" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_audit_events
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_audit_events" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_audit_events" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_access_grants
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_access_grants" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_access_grants" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

### vault_export_jobs
```
INSERT OK — id=1
UPDATE blocked — raw error: ERROR:  vault_immutability_violation: UPDATE on table "vault_export_jobs" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
DELETE blocked — raw error: ERROR:  vault_immutability_violation: DELETE on table "vault_export_jobs" is permanently prohibited. The vault schema is append-only. Insert new rows to record state changes.
CONTEXT:  PL/pgSQL function vault_immutability_guard() line 3 at RAISE
```

---

## 5. Design Notes

**Why append-only for status-tracking tables (vault_export_jobs, vault_access_grants):**
Status changes are recorded as new rows sharing the same `job_id` UUID (export jobs) or as explicit `grant_action='revoke'` rows (access grants). This preserves full history and is consistent with the immutability invariant for all 10 tables.

**No existing architecture substitution:**
No event-sourcing or stronger immutability mechanism was found to be in use for vault-scoped tables. The trigger-based approach is the standard Postgres pattern for this constraint.

**Test rows written during immutability testing:**
One row per table was INSERT-ed to test immutability. These rows remain in the tables (triggers prevent deletion). They are labeled with `component_type='module'`, `run_type='phase1_test'`, `event_type='immutability_test'`, `export_type='schema_dump'`, etc. They are inert test artifacts.

---

## 6. Files Changed This Session

| File | Action | sha256 |
|---|---|---|
| `docs/verification/vault-phase1-schema.sql` | created | `e41d5f6a0a1cac1c9d04e603a592f48c4f509bae8b54e808b093716c8b6e6740` |
| `docs/verification/vault-phase1-FINAL.md` | created | (this file — hash computed after commit) |

---

## 7. Phase 1 Closure Gate

**Status: pending Joel's sign-off.**

Per the standing protocol: do not start Phase 2 until Phase 1 is confirmed closed with Joel's explicit sign-off.

Open items before Phase 1 closure:
1. Joel's explicit sign-off on this record
2. `tools/verified_run.sh` operator-confirmed canonical still PENDING (vault-phase0-FINAL.md Section 21.4)
