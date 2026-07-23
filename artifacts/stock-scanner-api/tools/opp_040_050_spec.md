# OPP-040 to OPP-050 — Specification (No Code)

AIEM Options Pipeline Phase 5 — Operational Observability & Audit Export
Status: SPEC ONLY — no production code changes
Author: Phase 5 Verification Session
Date: 2026-07-23

---

## OPP-040: GET /stock-api/options-pipeline/candidates — read endpoint

**Purpose**: Expose the full decision context for every alert that passed
all gates, joined with its oe_decision_audit row, to support downstream
review, replay, and operator inspection.

**Method**: GET

**Auth**: `ADMIN_TOKEN` header required (same guard used on all admin routes)

**Query params**:
- `scan_date` (YYYY-MM-DD, optional): filter by `aiem_options_alerts.scan_date`; defaults to today ET
- `direction` (CALL|PUT, optional): filter by `aiem_options_alerts.direction`
- `min_score` (int 0–100, optional): filter by `aiem_options_alerts.req6_score >= min_score`
- `status` (OPEN|CLOSED|all, optional): filter `aiem_options_alerts.status`; default=all
- `limit` (int, optional): row cap; default 100; max 500
- `offset` (int, optional): pagination offset; default 0

**Response shape (JSON)**:
```json
{
  "scan_date": "2026-07-23",
  "total": 12,
  "results": [
    {
      "alert_id": "...",
      "ticker": "NVDA",
      "direction": "CALL",
      "req6_score": 74.2,
      "status": "OPEN",
      "created_at": "2026-07-23T09:47:11Z",
      "audit": {
        "decision_id": "84f58605c217435a90037f55",
        "verification_status": "PENDING",
        "engine_version": "options_v3.1",
        "db_version": "phase5.1",
        "is_test_record": false,
        "input_hash": "96b89095...",
        "output_hash": "c660a02d...",
        "parent_id": null,
        "identity_json": { ... },
        "technical_json": { ... },
        "options_intel_json": { ... },
        "probability_risk_json": { ... },
        "justification_json": { ... }
      }
    }
  ]
}
```

**Join logic**:
```sql
SELECT a.*, d.*
FROM aiem_options_alerts a
LEFT JOIN oe_decision_audit d
  ON d.decision_id = a.trace_id
  AND d.is_test_record = FALSE
WHERE a.scan_date = :scan_date
  [AND a.direction = :direction]
  [AND a.req6_score >= :min_score]
  [AND a.status = :status]
ORDER BY a.created_at DESC
LIMIT :limit OFFSET :offset
```

**Error cases**:
- 401: missing/invalid ADMIN_TOKEN
- 400: invalid scan_date format
- 500: DB failure (no fallback — this is an admin endpoint, not user-facing)

---

## OPP-041: GET /stock-api/options-pipeline/candidates/export — CSV export

**Purpose**: Machine-readable CSV dump of the same candidates join from
OPP-040, suitable for loading into Excel, Jupyter, or a backtesting harness.

**Method**: GET

**Auth**: `ADMIN_TOKEN` required

**Query params**: same as OPP-040 (scan_date, direction, min_score, status, limit)

**Response**: `Content-Type: text/csv` with `Content-Disposition: attachment`

**CSV columns** (one row per alert):
```
alert_id, ticker, direction, req6_score, status, created_at,
decision_id, verification_status, engine_version, db_version,
is_test_record, input_hash, output_hash, parent_id
```

JSONB columns (identity_json, technical_json, options_intel_json,
probability_risk_json, justification_json) are **excluded** from the CSV
to keep it flat. A separate OPP-043 endpoint handles JSONB export.

**Filename convention**: `options_candidates_{scan_date}_{timestamp}.csv`

---

## OPP-042: GET /stock-api/options-pipeline/audit/{decision_id} — single-row detail

**Purpose**: Retrieve the complete oe_decision_audit row for one decision_id,
including all JSONB blobs. Used by the AIEM Dashboard Phase B detail panel.

**Method**: GET

**Auth**: `ADMIN_TOKEN` required

**Path param**: `decision_id` (string, 24-char hex ID from oe_decision_audit)

**Response**:
```json
{
  "decision_id": "84f58605c217435a90037f55",
  "found": true,
  "is_test_record": false,
  "verification_status": "PENDING",
  "engine_version": "options_v3.1",
  "db_version": "phase5.1",
  "input_hash": "96b89095...",
  "output_hash": "c660a02d...",
  "parent_id": null,
  "created_at": "2026-07-23T09:47:11Z",
  "identity_json": { ... },
  "technical_json": { ... },
  "options_intel_json": { ... },
  "probability_risk_json": { ... },
  "justification_json": { ... },
  "chain": {
    "children": [ ... ],
    "parent": null
  }
}
```

**Chain resolution**: The response includes a `chain` block with:
- `parent`: the parent decision row summary (if `parent_id` is non-null)
- `children`: list of child decision row summaries (rows where `parent_id = this decision_id`)

**Not-found**: returns `{"found": false}` with HTTP 404

---

## OPP-043: GET /stock-api/options-pipeline/audit/{decision_id}/jsonb — JSONB export

**Purpose**: Return all five JSONB columns for a single audit row as a
structured JSON blob for debugging or archival purposes.

**Method**: GET  
**Auth**: `ADMIN_TOKEN` required  
**Path param**: `decision_id`  

**Response**:
```json
{
  "decision_id": "...",
  "identity_json": { ... },
  "technical_json": { ... },
  "options_intel_json": { ... },
  "probability_risk_json": { ... },
  "justification_json": { ... }
}
```

---

## OPP-044: PATCH /stock-api/options-pipeline/audit/{decision_id}/verify — set verification_status

**Purpose**: Allow an operator to set `verification_status` on a production
audit row. This is the *only* mutable field on a production row (per the
`trg_oe_dpl_immutable` trigger design; `trg_oe_decision_audit_immutable`
currently blocks even this — see TRACE-057b finding). Implementation must
confirm the effective trigger behavior at runtime and document which trigger
wins.

**Method**: PATCH  
**Auth**: `ADMIN_TOKEN` required  

**Body**:
```json
{ "verification_status": "VERIFIED" }
```

**Allowed values**: `PENDING`, `VERIFIED`, `FAILED`, `TAMPERED`

**Behavior**:
- Attempt `UPDATE oe_decision_audit SET verification_status=? WHERE decision_id=? AND is_test_record=FALSE`
- If trigger blocks (as observed in TRACE-057b), return `409 Conflict` with the trigger error
- If update succeeds, return `200 OK` with the updated row summary
- Never modify any other field

**Not-found**: 404

---

## OPP-045: GET /stock-api/options-pipeline/chain-integrity — chain health report

**Purpose**: Run the chain integrity LEFT JOIN query across all of
oe_decision_audit and return a summary showing chain health. Exposes the
same logic used in TRACE-052 as a live operational endpoint.

**Method**: GET  
**Auth**: `ADMIN_TOKEN` required  
**Query params**:
- `is_test_record` (true|false|all, optional): scope rows; default=false (production only)

**Response**:
```json
{
  "as_of": "2026-07-23T15:30:00Z",
  "total_rows": 345,
  "root_rows": 343,
  "child_rows": 2,
  "orphaned_rows": 0,
  "chain_health": "OK",
  "orphaned": []
}
```

`chain_health` is `"OK"` when `orphaned_rows = 0`, otherwise `"BROKEN"`.

`orphaned` is a list of `{decision_id, parent_id}` for any orphaned rows found.

---

## OPP-046: GET /stock-api/options-pipeline/hash-stats — hash field coverage

**Purpose**: Report the count and percentage of rows in oe_decision_audit
where `input_hash` or `output_hash` is NULL. Operationalizes TRACE-051.

**Method**: GET  
**Auth**: `ADMIN_TOKEN` required  

**Response**:
```json
{
  "as_of": "2026-07-23T15:30:00Z",
  "total_rows": 345,
  "rows_with_null_input_hash": 0,
  "rows_with_null_output_hash": 0,
  "hash_coverage_pct": 100.0,
  "verdict": "PASS"
}
```

`verdict` is `"PASS"` when both null counts are 0, otherwise `"FAIL"`.

---

## OPP-047: POST /stock-api/options-pipeline/audit/seed-test — test row seeding

**Purpose**: Insert a known test row (is_test_record=TRUE) into
oe_decision_audit with controlled field values for use in negative-control
testing and dashboard walkthrough demos. Must never create production rows.

**Method**: POST  
**Auth**: `ADMIN_TOKEN` required  

**Body**:
```json
{
  "ticker": "TEST_TICKER",
  "direction": "CALL",
  "parent_id": null,
  "verification_status": "PENDING",
  "note": "OPP-047 seeded for demo"
}
```

**Behavior**:
- Always sets `is_test_record=TRUE`
- Generates synthetic `input_hash` and `output_hash` via `hashlib.sha256`
- Populates `identity_json` with the seed payload including the note
- Returns `201 Created` with the new `decision_id`
- Caller is responsible for cleanup via OPP-048

**Reject if** `is_test_record` is explicitly set to `false` in the body (return 400)

---

## OPP-048: DELETE /stock-api/options-pipeline/audit/test-rows — bulk delete test rows

**Purpose**: Remove all is_test_record=TRUE rows from oe_decision_audit.
The immutability trigger allows DELETE on test rows; this endpoint
exposes that capability safely to operators.

**Method**: DELETE  
**Auth**: `ADMIN_TOKEN` required  
**Query params**:
- `older_than_hours` (int, optional): only delete test rows created more than N hours ago;
  default=1 (protect recently seeded rows)
- `dry_run` (bool, optional): if true, return count without deleting; default=false

**Response**:
```json
{
  "deleted": 7,
  "dry_run": false,
  "older_than_hours": 1
}
```

**Safety**: Production rows (is_test_record=FALSE) are never touched — the
WHERE clause always includes `AND is_test_record=TRUE`. The trigger also
independently enforces this.

---

## OPP-049: GET /stock-api/options-pipeline/negative-control-report — live TRACE-056/057/058 replay

**Purpose**: Re-run the three negative-control probes (TRACE-056, TRACE-057,
TRACE-058) against the live database and return a structured report. Intended
for scheduled integrity audits and dashboard health panels.

**Method**: GET  
**Auth**: `ADMIN_TOKEN` required  

**Response**:
```json
{
  "as_of": "2026-07-23T15:30:00Z",
  "probes": {
    "TRACE-056": {
      "description": "INSERT with NULL input_hash must fail NOT NULL constraint",
      "verdict": "PASS",
      "error_received": "null value in column input_hash violates not-null constraint"
    },
    "TRACE-057": {
      "description": "UPDATE identity_json on production row must be blocked by trigger",
      "verdict": "PASS",
      "error_received": "[DPL] oe_decision_audit production rows are immutable..."
    },
    "TRACE-058": {
      "description": "INSERT child with nonexistent parent_id must be blocked by FK",
      "verdict": "PASS",
      "error_received": "violates foreign key constraint oe_decision_audit_parent_id_fkey"
    }
  },
  "summary": "3/3 PASS",
  "all_pass": true
}
```

**Implementation notes**:
- Each probe runs inside its own `BEGIN` / `ROLLBACK` block — no permanent changes
- TRACE-058 inserts a temporary parent row (is_test_record=TRUE) inside the transaction
  and rolls back after the FK probe
- Probe order is always 056 → 057 → 058
- If any probe returns an unexpected result (wrong error, no error), `verdict="FAIL"`
  and the actual DB response is captured in `error_received`

---

## OPP-050: GET /stock-api/options-pipeline/formula-audit — FIN formula verification summary

**Purpose**: Report the cached result of the last FIN-001..042 formula
verification run (tools/fin_verify_phase5.py output), surfacing any FAIL
items as a live health signal for the dashboard.

**Method**: GET  
**Auth**: `ADMIN_TOKEN` required  

**Response**:
```json
{
  "last_run_at": "2026-07-23T15:45:00Z",
  "total_items": 42,
  "pass": 42,
  "fail": 0,
  "partial": 0,
  "not_implemented": 0,
  "verdict": "ALL_PASS",
  "failures": [],
  "source_script": "tools/fin_verify_phase5.py",
  "sha256_at_last_run": "..."
}
```

**Data source**: The endpoint reads from a `formula_audit_cache` table (or
a flat JSON file at `tools/fin_audit_result.json`) written by the
verification script the last time it was run. It does NOT re-execute the
script on request (the script takes ~10s and imports heavy dependencies).

**Cache invalidation**: Any change to the formula source files
(greeks.py, payoff.py, probability.py, aiem_options_phase3.py,
aiem_options_pipeline.py, ase_prob_ev_verification.py, aiem_optprob.py)
should invalidate the cache. A post-deploy hook that re-runs the script
and writes the result file is the recommended pattern.

**Schema for formula_audit_cache table** (if DB-backed):
```sql
CREATE TABLE IF NOT EXISTS formula_audit_cache (
  id          SERIAL PRIMARY KEY,
  run_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  total       INT NOT NULL,
  pass_n      INT NOT NULL,
  fail_n      INT NOT NULL,
  partial_n   INT NOT NULL,
  not_impl_n  INT NOT NULL,
  failures    JSONB NOT NULL DEFAULT '[]',
  script_sha  TEXT NOT NULL,
  result_json JSONB NOT NULL
);
```

Only the most recent row is used; older rows are retained for history.

---

## Cross-cutting constraints for OPP-040..050

1. **ADMIN_TOKEN gate**: Every endpoint above requires `Authorization: Bearer {ADMIN_TOKEN}`
   or the `X-Admin-Token: {ADMIN_TOKEN}` header (matching whichever convention the existing
   admin routes use). A missing or wrong token returns 401 with no body.

2. **is_test_record filter**: All read endpoints that query oe_decision_audit must
   default to `WHERE is_test_record=FALSE` (production rows only) unless the caller
   explicitly passes `?include_test=true`.

3. **No JSONB mutation**: OPP-044 (verify) is the only write endpoint that touches
   oe_decision_audit, and it may only set `verification_status`. All other
   oe_decision_audit fields are immutable and must never be updated by any OPP endpoint.

4. **Trigger-awareness**: OPP-044 must handle the case where `trg_fn_oe_decision_audit_immutable`
   blocks `verification_status` updates (observed in TRACE-057b). The endpoint returns
   a 409 with the trigger message rather than crashing with a 500.

5. **Rollback safety**: OPP-049's negative-control probes must always roll back their
   test transactions. A crash mid-probe must not leave orphaned test rows in production.

6. **No in-memory fallback**: These are admin/audit endpoints. Returning stale cache
   data without a DB connection is not acceptable — return 503 instead.
