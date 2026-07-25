-- AIEM Verification Vault — Phase 1 Schema
-- Scope: AIEM components only. Options Engine and StockScanner AI excluded.
-- Design: append-only / immutable. No UPDATE or DELETE is permitted on any vault table.
--         Status progressions (export jobs, access grants) are represented as new rows.
-- Applied: 2026-07-25
-- git HEAD at time of application: 62a1077b9afed8746ea290daa63baae27b8bbb4c

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- Shared immutability trigger function
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. vault_components
--    Registry of every AIEM component discovered during verification.
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. vault_component_relationships
--    Directed edges between components (calls, imports, writes_to, reads_from).
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. vault_verification_runs
--    One row per verification phase run. Immutable once written.
--    "Closing" a run = new row with status='closed', not an UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. vault_component_verifications
--    Per-component result rows within a verification run.
--    result values: 'pass' | 'fail' | 'partial' | 'cleared-to-proceed' | 'accepted-risk'
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. vault_evidence_artifacts
--    Raw evidence files referenced by verification runs.
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. vault_source_snapshots
--    Point-in-time source file hashes tied to a git commit.
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. vault_deployment_snapshots
--    State of the deployment at a point in time.
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. vault_audit_events
--    Append-only general audit log for all vault operations.
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. vault_access_grants
--    Append-only access log. Revocations are new rows (grant_action='revoke'),
--    never UPDATEs to existing grant rows.
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. vault_export_jobs
--     Append-only job tracking. Status progression = new rows sharing job_id.
-- ─────────────────────────────────────────────────────────────────────────────
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
