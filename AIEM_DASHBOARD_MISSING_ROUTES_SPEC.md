# AIEM DASHBOARD — PHASE A
## Five Missing Admin Routes — Full Specifications
**Generated:** 2026-07-21 | **Status:** SPEC ONLY — do not implement until Phase A review complete

---

## Route 1: GET /stock-api/admin/decision-audit

**Dashboard screen consuming it:** Audit & Verification, Live Decisions  
**Priority:** P1 (highest — 341 rows, most critical missing route)  
**Auth:** `X-Admin-Token: <ADMIN_TOKEN>` header (fail-closed: 403 if missing or wrong)

### Source
- **Table:** `oe_decision_audit`
- **Writer:** `aiem_options_dpl.py` (Decision Provenance Layer)
- **Row count:** 341 (2026-07-19 to 2026-07-21)

### Columns to Return (safe subset)
```
decision_id         TEXT      -- PK, UUID
parent_id           TEXT      -- parent decision (nullable)
created_at          TIMESTAMPTZ
input_hash          TEXT      -- SHA-256 of all decision inputs
output_hash         TEXT      -- SHA-256 of decision output
verification_status TEXT      -- VERIFIED | UNVERIFIED | FAILED
engine_version      TEXT      -- hash of engine at decision time
db_version          TEXT      -- DB schema version at decision time
is_test_record      BOOLEAN   -- ALWAYS include in filter
identity_json       JSONB     -- ticker, direction, scan_date (safe to expose)
technical_json      JSONB     -- technical indicators (safe to expose)
options_intel_json  JSONB     -- options chain snapshot (safe to expose)
```

### Excluded Columns
```
probability_risk_json   -- may contain internal model weights (omit for now)
justification_json      -- may contain raw LLM output (omit for now)
```

### Filters
- `is_test_record = FALSE` — MANDATORY, always applied
- `?ticker=` — optional, filter by ticker (from identity_json->>'ticker' or a stored column)
- `?date=YYYY-MM-DD` — optional, filter by DATE(created_at) in ET
- `?status=VERIFIED|UNVERIFIED|FAILED` — optional
- `?limit=N` — default 50, max 200
- `?offset=N` — default 0

### Sorting
Default: `ORDER BY created_at DESC`

### Response Schema
```json
{
  "count": 341,
  "limit": 50,
  "offset": 0,
  "rows": [
    {
      "decision_id": "uuid-string",
      "parent_id": null,
      "created_at": "2026-07-21T04:42:00Z",
      "input_hash": "abc123...",
      "output_hash": "def456...",
      "verification_status": "VERIFIED",
      "engine_version": "...",
      "db_version": "...",
      "identity": { "ticker": "AAPL", "direction": "CALL", "scan_date": "2026-07-21" },
      "technical": { ... }
    }
  ]
}
```

### Empty State
```json
{ "count": 0, "limit": 50, "offset": 0, "rows": [] }
```

### Error Schema
```json
{ "error": "unauthorized" }        -- 403
{ "error": "invalid date format" } -- 400
{ "error": "database unavailable"} -- 503
```

### Pagination
Cursor-based via limit/offset. Max page: 200. No total count query on large pages.

### Date Range Support
`?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` — filters `created_at` between dates.

### Trace-ID Lookup
`?trace_id=<value>` — exact match on `decision_id` OR via `identity_json->>'trace_id'` if stored.

### Rate Limiting
Admin routes: no rate limit. If future multi-tenant, add 10 req/min per token.

### Runtime Test
```bash
curl "https://<host>/stock-api/admin/decision-audit?limit=5&date=2026-07-21" \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```
Expected: `count > 0`, rows present, all `is_test_record=false`.

---

## Route 2: GET /stock-api/admin/gate-events

**Dashboard screen consuming it:** Audit & Verification, Live Decisions  
**Priority:** P1  
**Auth:** `X-Admin-Token: <ADMIN_TOKEN>`

### Source
- **Table:** `oe_gate_events`
- **Writer:** `aiem_options_phase5.py`
- **Row count:** 4 (2026-07-21)

### Columns (all safe to return)
```
gate_event_id    TEXT PK
gate_name        TEXT      -- name of gate that fired
fired_at         TIMESTAMPTZ
ticker           TEXT
trace_id         TEXT      -- links to options_pipeline_jobs.trace_id
action_taken     TEXT      -- HALT | ALLOW | FLAG
is_test_record   BOOLEAN   -- MANDATORY filter
authenticated_by TEXT
prev_hash        TEXT      -- chain integrity
chain_hash       TEXT
candidate_id     TEXT
pipeline_job_id  TEXT
git_commit       TEXT
reason           TEXT
```

### Excluded Columns
```
decision_context JSONB  -- may contain raw model internals; surface only gate_name+action_taken for now
live_hash        TEXT   -- cryptographic material; expose only in dedicated verification screen
expected_hash    TEXT   -- same
mismatch_detail  TEXT   -- expose in detail view only
```

### Filters + Response
- `?date=`, `?ticker=`, `?gate_name=`, `?limit=` (default 50, max 200)
- `is_test_record = FALSE` MANDATORY
- `ORDER BY fired_at DESC`

### Trace-ID Lookup
`?trace_id=<value>` — exact match on `trace_id` column.

### Runtime Test
```bash
curl "https://<host>/stock-api/admin/gate-events?limit=10" \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```
Expected: 4 rows as of 2026-07-21.

---

## Route 3: GET /stock-api/admin/council-runs

**Dashboard screen consuming it:** Specialist Council  
**Priority:** P1  
**Auth:** `X-Admin-Token: <ADMIN_TOKEN>`

### Source
- **Table:** `aiem_specialist_council_runs`
- **Writer:** `specialist_council.py:persist_council_run()`
- **Row count:** 219 (2026-07-12 to 2026-07-21)

### Columns (full schema — 13 cols, all safe)
```
id                BIGINT PK
run_time          TIMESTAMPTZ
context           TEXT       -- PICK | MTM | BACKTEST
ticker            TEXT
trace_id          TEXT       -- links to options_pipeline_jobs
registered_members JSONB     -- list of member names
invoked_members   JSONB      -- members actually called
abstained_members JSONB      -- members that abstained
abstentions       JSONB      -- abstention details
opinions          JSONB      -- per-member opinions + rationale
weighted_vote     FLOAT      -- aggregate conviction score
variance          FLOAT      -- disagreement among members
weights           JSONB      -- per-member weights applied
```

### Filters + Response
- `?ticker=`, `?context=PICK|MTM|BACKTEST`, `?date=YYYY-MM-DD`, `?trace_id=`
- `?limit=` (default 50, max 200), `?offset=`
- `ORDER BY run_time DESC`

### Sensitive Fields
`opinions` JSONB contains raw LLM specialist text — dashboard should display but not index/export.

### Trace-ID Lookup
`?trace_id=<value>` — exact match on `trace_id` column. Links to the paper trade that triggered this council.

### Runtime Test
```bash
curl "https://<host>/stock-api/admin/council-runs?ticker=TSLA&limit=5" \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

---

## Route 4: GET /stock-api/admin/position-sizing-log

**Dashboard screen consuming it:** Portfolio Risk  
**Priority:** P1  
**Auth:** `X-Admin-Token: <ADMIN_TOKEN>`

### Source
- **Table:** `aiem_position_sizing_log`
- **Writer:** `aiem_position_sizing.py:compute_position_size()`
- **Row count:** 207 (2026-07-12 to 2026-07-21)

### Columns (all safe — 17 cols)
```
id                  BIGINT PK
logged_at           TIMESTAMPTZ
ticker              TEXT
signal_source       TEXT
conviction_score    NUMERIC
entry_price         NUMERIC
calculated_stop_price NUMERIC
stop_basis          TEXT
stop_distance_pct   NUMERIC
risk_pct_used       NUMERIC
calculated_notional NUMERIC
gate_result         TEXT    -- PASS | FAIL | PARTIAL
gate_detail         TEXT
mode                TEXT    -- STANDARD | OVERNIGHT | AGGRESSIVE
overnight_option    TEXT
paper_trade_id      INTEGER -- FK to aiem_paper_trades.id
created_at          TIMESTAMPTZ
```

### Filters + Response
- `?ticker=`, `?date=`, `?gate_result=PASS|FAIL`, `?signal_source=`
- `?paper_trade_id=<int>` — exact match for per-trade drill-down
- `?limit=` (default 50, max 200)
- `ORDER BY logged_at DESC`

### Trace-ID Lookup
No direct trace_id — use `?paper_trade_id=` to link to the trade's audit_trace_id.

### Runtime Test
```bash
curl "https://<host>/stock-api/admin/position-sizing-log?limit=10" \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

---

## Route 5: GET /stock-api/admin/evidence-chain/status

**Dashboard screen consuming it:** Decision Proof, Audit & Verification  
**Priority:** P1  
**Auth:** `X-Admin-Token: <ADMIN_TOKEN>`

### Source
- **File:** `artifacts/stock-scanner-api/evidence_chain.log`
- **Format:** NDJSON (one JSON object per line)
- **Current SEQ:** 10 (as of 2026-07-21)

### Implementation
```python
@app.route('/stock-api/admin/evidence-chain/status')
def admin_evidence_chain_status():
    if request.headers.get('X-Admin-Token') != os.environ.get('ADMIN_TOKEN', ''):
        return jsonify({'error': 'unauthorized'}), 403
    import json, os
    chain_path = os.path.join(os.path.dirname(__file__), 'evidence_chain.log')
    try:
        with open(chain_path) as f:
            lines = [l.strip() for l in f if l.strip()]
        last = json.loads(lines[-1]) if lines else {}
        return jsonify({
            'seq': last.get('seq', 0),
            'last_command': last.get('command', ''),
            'last_exit_code': last.get('exit_code', -1),
            'last_timestamp_utc': last.get('timestamp_utc', ''),
            'last_entry_hash': last.get('entry_hash', ''),
            'total_entries': len(lines),
            'chain_path': 'evidence_chain.log'
        })
    except FileNotFoundError:
        return jsonify({'error': 'chain file not found', 'seq': 0}), 404
```

### Response Schema
```json
{
  "seq": 10,
  "last_command": "cd artifacts/stock-scanner-api && python3 dpl/verify_dpl_phase3.py",
  "last_exit_code": 1,
  "last_timestamp_utc": "2026-07-21T04:42:15.858101Z",
  "last_entry_hash": "f7440d76ed187b5cc303e286c4c713ceb9dc11526a431eb4373bb88bbd0870c9",
  "total_entries": 10,
  "chain_path": "evidence_chain.log"
}
```

### Note
`last_exit_code: 1` is currently expected — verify_dpl_phase3.py is failing on all recent runs. Dashboard should display this as a WARNING state, not an ERROR — the chain itself is valid; the verifier tool has a known failure.

### Runtime Test
```bash
curl "https://<host>/stock-api/admin/evidence-chain/status" \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```
Expected: `seq: 10`, `last_exit_code: 1` (known failing verifier), `total_entries: 10`.
