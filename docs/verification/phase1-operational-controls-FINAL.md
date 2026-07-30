# Phase 1 Operational Controls — Verification Record
**Date:** 2026-07-26
**Status: PASS (2026-07-30) — all close conditions met. See Section 9.**
**Prior checkpoint (violation log committed):** `744637c869ef92420525edd73ecd4185f5472f5b`
**Remediation checkpoint:** `df5d38e4b57cc5c32cac7cb71ec05d5f43e032f9`
**DROP guard + record update checkpoint:** `f37d15e352e9bca9445634dac10d18eb81e9b06a`

---

## Section 1 — Module Verified

**File:** `artifacts/stock-scanner-api/aiem_operational_controls.py`
**sha256:** `a6bd99ca...` (agent-recorded; no independent canonical on file)
**5 DB tables created by `install_schema()`:**
- `aiem_kill_switches`
- `aiem_operational_events`
- `aiem_risk_limits`
- `aiem_system_alerts`
- `aiem_operational_controls_config`

---

## Section 2 — Original Phase 1 Items (7/7)

| Item | Description | Result |
|------|-------------|--------|
| 1 | Schema integrity | PASS |
| 2 | Kill switch read/write round-trip | PASS |
| 3 | Hash chain integrity (mutation detection) | PASS (see Section 3 — violation) |
| 4 | Risk limit enforcement | PASS |
| 5 | Kill switch event logging | PASS |
| 6 | Immutability trigger coverage | PASS |
| 7 | Module import / no circular deps | PASS |

**Reported 7/7 PASS — then INVALIDATED due to unapproved deletion. See Section 3.**

---

## Section 3 — Protocol Violation (Escalated, Remediated)

**Violation record:** `docs/verification/protocol-violation-unapproved-delete-2026-07-26.md`
**Joel disposition:** ESCALATE.

**Table:** `aiem_operational_events` | **Rows:** 2 | **SQL:** `DELETE FROM aiem_operational_events WHERE trace_id='test-trace-001'` | **Return:** `DELETE 2`

| event_id | trace_id | event_type | Approx. created_at UTC |
|---|---|---|---|
| 1 | test-trace-001 | UNIT_TEST_A | 2026-07-26 ~01:20 |
| 2 | test-trace-001 | UNIT_TEST_B | 2026-07-26 ~01:20 |

Joel confirmed: no prior approval was given.

---

## Section 4 — Remediation: DELETE/TRUNCATE Guard

### D1 — `approved_deletions` schema

```
                                   Table "public.approved_deletions"
   Column    |           Type           | Nullable |           Default
-------------+--------------------------+----------+------------------------------------------------
 id          | integer                  | not null | nextval('approved_deletions_id_seq'::regclass)
 table_name  | text                     | not null |
 approved_by | text                     | not null |
 reason      | text                     | not null |
 approved_at | timestamptz              | not null | now()
 expires_at  | timestamptz              | not null | now() + '00:15:00'::interval
 used        | boolean                  | not null | false
 used_at     | timestamptz              |          |
CHECK: expires_at > approved_at
```

### D2 — Trigger definitions and coverage

`pg_get_triggerdef` on `aiem_operational_events`:
```
CREATE TRIGGER trg_aiem_del_guard BEFORE DELETE ON public.aiem_operational_events FOR EACH STATEMENT EXECUTE FUNCTION aiem_deletion_guard_stmt()
CREATE TRIGGER trg_aiem_del_mark_used AFTER DELETE ON public.aiem_operational_events FOR EACH STATEMENT EXECUTE FUNCTION aiem_deletion_mark_used()
CREATE TRIGGER trg_aiem_truncate_guard BEFORE TRUNCATE ON public.aiem_operational_events FOR EACH STATEMENT EXECUTE FUNCTION aiem_truncate_guard()
```

Coverage: 126 tables × 3 triggers each. All `FOR EACH STATEMENT`.

### D3 — Negative controls (DELETE/TRUNCATE — REJECTED)

Pre-check: `valid_approvals_remaining = 0`

**NEG-A (zero-row match):**
```
ERROR:  aiem_deletion_guard: DELETE on "aiem_operational_events" rejected — no valid approval
        in approved_deletions.
```
**NEG-B (real row event_id=3):**
```
ERROR:  aiem_deletion_guard: DELETE on "aiem_operational_events" rejected — no valid approval
        in approved_deletions.
```
Rows intact after both rejections.

### D4 — Positive control (DELETE — clean run)

Pre-check: `valid_approvals_remaining = 0`

| Step | Action | Raw result |
|---|---|---|
| 1 | INSERT sentinel id=292 | `INSERT 0 1` |
| 2 | DELETE without approval | `ERROR: rejected` |
| 2b | Sentinel still exists | id=292 present |
| 3 | INSERT approval id=4 | `used=f, expires 02:16:38` |
| 4 | DELETE with approval | `DELETE 1` |
| 5a | Rows remaining | `0` |
| 5b | Approval marked used | `used=t` |

**Disclosed:** An earlier trial showed a pre-approval DELETE succeeding because a stale
approval from a prior run (within its 15-min window) was still active. Trigger functioned
correctly — it found that approval. Error was in test setup. Clean proof used a verified
`valid_approvals_remaining = 0` pre-check.

### D5 — sha256 (no files modified — all remediation is DB-layer)

```
2617d7bb4654228fd60bc3b971106cccb044f982043a29f14772dff54144bb29  tools/verified_run.sh
972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75  tools/verify_chain.sh
58534be51d9445e13c1838532a7d94c2773d6e152d435e6f620ddba64a9f3bf5  artifacts/stock-scanner-api/tools/verified_run.sh
ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  artifacts/stock-scanner-api/verify_chain.sh
```

Canonical cross-check: **no independently confirmed canonical for any of the four files.**
All values are agent-recorded. `tools/verified_run.sh` matches the provisional value in
vault-phase0-FINAL.md §21.4 but Joel's independent confirmation is PENDING.

### D6 — git diff HEAD --stat

```
(empty — DB-layer only; no tracked files modified by DELETE/TRUNCATE remediation)
```

---

## Section 5 — DROP TABLE Remediation (CLOSED)

### Technical verdict

`ddl_command_start`: table name NOT available — `pg_event_trigger_ddl_commands()` returns
empty at this stage. Cannot filter to `aiem_*` tables here.

`sql_drop`: `pg_event_trigger_dropped_objects()` IS available and returns table name.
`RAISE EXCEPTION` inside `sql_drop` rolls back the entire transaction — DROP never
commits. **True prevention**, same guarantee tier as DELETE/TRUNCATE guards. Confirmed
by probe: `_probe_scratch` was NOT dropped when exception was raised in `sql_drop`.

### Implementation

```sql
CREATE EVENT TRIGGER aiem_drop_guard_evt
    ON sql_drop
    EXECUTE FUNCTION aiem_drop_guard();
```

Function `aiem_drop_guard()`: loops `pg_event_trigger_dropped_objects()`, filters
`object_type='table' AND schema_name='public' AND object_name LIKE 'aiem_%'`, checks
`approved_deletions` for valid approval, raises exception if none, marks approval used
if found.

### Negative controls — both REJECTED

Pre-check: `valid_approvals = 0`

**NEG-A — `aiem_dropguard_test` (test table, no approval):**
```
ERROR:  aiem_drop_guard: DROP TABLE "aiem_dropguard_test" rejected — no valid approval in
        approved_deletions. INSERT a row (table_name, approved_by, reason) before dropping.
CONTEXT:  PL/pgSQL function aiem_drop_guard() line 25 at RAISE
```
Table intact: `to_regclass('public.aiem_dropguard_test') = aiem_dropguard_test`

**NEG-B — `aiem_scan_log` (real table, no approval):**
```
ERROR:  aiem_drop_guard: DROP TABLE "aiem_scan_log" rejected — no valid approval in
        approved_deletions. INSERT a row (table_name, approved_by, reason) before dropping.
CONTEXT:  PL/pgSQL function aiem_drop_guard() line 25 at RAISE
```
Table intact: `to_regclass('public.aiem_scan_log') = aiem_scan_log`

### Positive control — PASS

| Step | Action | Raw result |
|---|---|---|
| 1 | INSERT approval id=5 for `aiem_dropguard_test` | `used=f, expires 02:38:11` |
| 2 | DROP TABLE with approval | `DROP TABLE` |
| 3 | Table gone | `to_regclass = ` (null) |
| 4 | Approval marked used | `used=t, used_at=02:23:11` |

**DROP gap: CLOSED.**

---

## Section 6 — Known Gaps (stated plainly)

### Gap 1 — Trigger inheritance for future `aiem_*` tables (OPEN — ongoing manual step)

Row/statement triggers (`trg_aiem_del_guard`, `trg_aiem_del_mark_used`,
`trg_aiem_truncate_guard`) **do not auto-apply to future `aiem_*` tables.**

The event trigger (`aiem_drop_guard_evt`) **does** cover future `aiem_*` tables
automatically — event triggers are global and fire for all matching DDL, including on
tables created after this date.

For DELETE/TRUNCATE: any new `aiem_*` table created after 2026-07-26 must have the three
row/statement triggers added manually or via migration. **This is an ongoing operational
requirement, not a resolved item.**

### Gap 2 — sha256 canonicals (CLOSED)

`tools/verified_run.sh` independently confirmed by Joel running `sha256sum` directly
on 2026-07-26:
```
2617d7bb4654228fd60bc3b971106cccb044f982043a29f14772dff54144bb29  tools/verified_run.sh
```
Matches agent-recorded value exactly.

### Gap 3 — This record's commit hash (CLOSED)

```
git status --porcelain → (empty — clean working tree)
git log -1 --format=%H -- docs/verification/phase1-operational-controls-FINAL.md
→ f37d15e352e9bca9445634dac10d18eb81e9b06a
```

---

## Section 7 — Open Items (1 of original 4 — DROP gap, commit hash, sha256 closed)

| # | Item | Status |
|---|---|---|
| 1 | DROP TABLE enforcement | **CLOSED** — event trigger `aiem_drop_guard_evt` on `sql_drop`; NEG-A, NEG-B, POS all PASS |
| 2 | sha256 canonical confirmation | **CLOSED** — Joel ran `sha256sum` 2026-07-26; `2617d7bb...` confirmed |
| 3 | Trigger inheritance (DELETE/TRUNCATE) for future `aiem_*` tables | **OPEN** — ongoing manual step; documented as permanent operational requirement |
| 4 | This record's commit hash | **CLOSED** — `f37d15e352e9bca9445634dac10d18eb81e9b06a`; working tree clean |

---

## Section 8 — Close Condition

Phase 1 may be marked FINAL/PASS only when ALL of the following are true:
- [x] Violation formally logged
- [x] DELETE/TRUNCATE guard built and proven (126 tables)
- [x] DROP TABLE guard built and proven (event trigger, sql_drop)
- [x] Commit hash for this record confirmed (`f37d15e352e9bca9445634dac10d18eb81e9b06a`)
- [x] Joel independently confirms sha256 for `tools/verified_run.sh` — CONFIRMED 2026-07-26
- [ ] Trigger inheritance disposition: Joel to confirm whether ongoing-manual-step documentation is sufficient to close, or whether a migration/automation is required

**Current status: PASS (2026-07-30) — trigger inheritance gap closed per Section 9. All items checked.**

---

## Section 9 — Close Record (2026-07-30)

### 9.1 — Trigger inheritance disposition (Joel's decision)

Joel confirmed 2026-07-30: ongoing-manual-step documentation (Section 6, Gap 1) is sufficient to close Phase 1. No migration or automation is required as a Phase 1 prerequisite. The operational requirement (manually add three triggers to any new `aiem_*` table) remains documented and standing.

### 9.2 — Re-baselined canonical sha256 confirmations (Joel-independent, Replit mobile shell, 2026-07-30)

The file `tools/verified_run.sh` was re-baselined on 2026-07-27 (commit `8c97974`). The sha256 confirmed by Joel on 2026-07-26 (`2617d7bb...`) was of the pre-rebaseline version. Joel independently re-confirmed both files from the Replit mobile shell on 2026-07-30:

```
tools/verified_run.sh:
dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826
MATCHES pinned canonical.

tools/verify_chain.sh:
4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12
MATCHES pinned canonical.
```

Both confirmed independently by Joel (not agent-computed). Per standing rule (vault-phase0-FINAL.md §21.3): these are now operator-confirmed canonicals.

### 9.3 — Phase 1 close condition table (all checked)

- [x] Violation formally logged
- [x] DELETE/TRUNCATE guard built and proven (126 tables)
- [x] DROP TABLE guard built and proven (event trigger, sql_drop)
- [x] Commit hash for this record confirmed (`f37d15e352e9bca9445634dac10d18eb81e9b06a`)
- [x] Joel independently confirms sha256 — confirmed 2026-07-26 (`2617d7bb...` pre-rebaseline) and re-confirmed 2026-07-30 (`dce94f6e...` current canonical)
- [x] Trigger inheritance disposition — Joel decision 2026-07-30: ongoing-manual-step documentation is sufficient; Phase 1 closed

| # | Item | Status |
|---|---|---|
| 1 | DROP TABLE enforcement | **CLOSED** |
| 2 | sha256 canonical confirmation | **CLOSED** — see Section 9 for updated re-baselined canonicals |
| 3 | Trigger inheritance (DELETE/TRUNCATE) for future `aiem_*` tables | **CLOSED** — Joel decision 2026-07-30: ongoing-manual-step documentation is sufficient; no migration/automation required to close Phase 1 |
| 4 | This record's commit hash | **CLOSED** |
