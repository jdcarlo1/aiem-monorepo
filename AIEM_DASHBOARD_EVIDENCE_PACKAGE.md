# AIEM DASHBOARD — PHASE A RAW EVIDENCE PACKAGE
**Generated:** 2026-07-21 | **Git Commit:** 9f1f406 | **Phase A scope:** Read-only, zero code changes

---

## CORRECTION LOG (vs Phase A Draft Reports)
The following facts were incorrect in the initial reports and are corrected here with raw evidence:

| Item | Draft Said | Raw Evidence Says | Evidence Command |
|------|-----------|-------------------|-----------------|
| Total DB tables | 580 | 364 | `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'` |
| d2_trace_id column | "stored in aiem_paper_trades" | DOES NOT EXIST as a column — paper trades use `audit_trace_id` | `SELECT table_name FROM information_schema.columns WHERE column_name='d2_trace_id'` → 0 rows |
| Evidence chain SEQ | "SEQ=61" | SEQ=10 (last line of evidence_chain.log) | `tail -1 artifacts/stock-scanner-api/evidence_chain.log` |
| Auth header format | "Authorization: Bearer" | `X-Admin-Token: <value>` | `grep -n "X-Admin-Token" main.py` → line 11494 |
| Total scheduled jobs | "7 (options scheduler)" | 274 (main.py BackgroundScheduler) | `GET /stock-api/admin/scheduler-jobs` → job_count=274 |

---

## 1. Repository Inventory

**Command:**
```bash
find artifacts/stock-scanner-api/ -name "*.py" | wc -l
sha256sum artifacts/stock-scanner-api/main.py
```

**Output:**
- Python modules: 239
- main.py SHA-256: `ee8489ed5d7d5233e7f728a44b99e02606e5ad6c68034289d881eb9a10e6b423`
- Total lines in main.py: ~69,000+
- API routes: 333 (`grep -c "^@app.route" main.py`)

---

## 2. Database Schema Inventory

**Command:**
```sql
SELECT COUNT(*) FROM information_schema.tables 
WHERE table_schema='public' AND table_type='BASE TABLE';
```
**Result:** 364

**Command:**
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE'
ORDER BY table_name;
```
**Result (sample):** Full 364-table list includes aiem_* (110), oe_* (45), d3_* (25), backup (6), other (178)

---

## 3. Row Count Queries (confirmed live)

**Command:** Individual `SELECT COUNT(*) FROM <table>` per table

**Results:**
```
polygon_market_daily:              3,346,234
polygon_indicators_daily:          3,261,314
td_intraday_cache:                   145,669
ticker_market_cap_cache:              13,094
oe_classification_correction_ledger:  6,298
telegram_alert_ledger:                1,144
unusual_calls_log:                    8,212
behavioral_pattern_matches:           1,286
candlestick_confluence_signals:         269
cta_trigger_scan:                     3,500
oe_decision_audit:                      341
aiem_pipeline_audit_log:                284
signal_fire_log:                        283
aiem_verification_log:                  358
aiem_specialist_council_runs:           219
aiem_position_sizing_log:               207
d3_governance_decisions:                 94
aiem_research_audit_sessions:            93
aiem_supervisor_loop_audit:              88
aiem_supervisor_event_log:              194
paper_trade_watchdog_heartbeat:       2,321
oe_indicator_snapshots:               1,739
oe_scheduler_trace:                      67
oe_decision_replay_inputs:              312
oe_legacy_replay_exceptions:            305
aiem_probability_engine_daily_picks:     10
aiem_probability_engine_predictions:     10
aiem_probability_engine_live_queries:    46
signal_outcomes:                         95
aiem_process_predictions:                60
aiem_paper_trades:                       31
aiem_paper_execution_log:                20
options_pipeline_jobs:                   20
paper_trade_job_ledger:                   5
daily_pipeline_runs:                      6
regime_history:                          42
opening_snapshots:                      116
options_structure_scan:                 406
bull_bear_debates:                       11
polygon_rvol_scan:                      110
aiem_signal_discoveries:                  5
oe_gate_events:                           4
EMPTY_TABLES (0 rows):                  155 of 364
```

---

## 4. Timestamp Queries (confirmed live)

**Command:** Individual `SELECT MIN(col), MAX(col) FROM <table>` per table (separate connections)

**Results:**
```
aiem_paper_trades.created_at:           2026-07-12 to 2026-07-21
aiem_specialist_council_runs.run_time:  2026-07-12 to 2026-07-21
aiem_supervisor_loop_audit.created_at:  2026-07-14 to 2026-07-21
aiem_position_sizing_log.logged_at:     2026-07-12 to 2026-07-21
options_pipeline_jobs.created_at:       2026-07-16 to 2026-07-21
paper_trade_job_ledger.created_at:      2026-07-15 to 2026-07-21
paper_trade_watchdog_heartbeat.last_alive: 2026-07-15 to 2026-07-21 15:46
aiem_probability_engine_daily_picks.created_at: 2026-07-16 to 2026-07-21
aiem_process_predictions.created_at:    2026-07-14 to 2026-07-21
polygon_rvol_scan.scan_date:            2026-07-10 to 2026-07-20
telegram_alert_ledger.sent_at:          2026-07-13 to 2026-07-21
regime_history.recorded_at:             2026-07-11 to 2026-07-21
aiem_supervisor_event_log.created_at:   2026-07-11 to 2026-07-21
cta_trigger_scan.scan_date:             2026-07-13 to 2026-07-21
aiem_pipeline_audit_log.logged_at:      2026-07-11 to 2026-07-21
oe_decision_audit.created_at:           2026-07-19 to 2026-07-21
oe_gate_events.fired_at:               2026-07-21 04:38 to 2026-07-21 05:11
```

---

## 5. API Route Enumeration

**Command:** `grep -n "^@app.route" artifacts/stock-scanner-api/main.py | wc -l`
**Result:** 333

**Command:** `grep -n "^@app.route" artifacts/stock-scanner-api/main.py | awk -F'"' '{print $2}'`
**Result:** Full list of 333 routes (lines 150–69283, all in main.py)

---

## 6. Scheduler Status (live)

**Command:** `GET /stock-api/admin/scheduler-jobs` with header `X-Admin-Token: <ADMIN_TOKEN>`
**Result:**
```json
{
  "job_count": 274,
  "jobs": [
    {"id": "poll_ask_sms", "trigger": "interval[0:00:30]", "next_run": "..."},
    {"id": "candidate_intake_poll", "trigger": "interval[0:02:00]", ...},
    {"id": "midday_unusual_calls", "trigger": "cron[day_of_week='mon-fri', hour='12', minute='5']", ...},
    ... (274 total jobs)
  ]
}
```

**Options pipeline scheduler (separate process):**
```
paper_trade_job_ledger:
  id=64  date=2026-07-21  status=COMPLETED  trigger=startup_recovery  picks=3
  id=63  date=2026-07-20  status=COMPLETED  trigger=internal_watchdog picks=4
  id=31  date=2026-07-17  status=SKIPPED    trigger=startup_recovery  NO_CANDIDATES
  id=12  date=2026-07-16  status=SKIPPED    trigger=startup_recovery  NO_CANDIDATES
  id=1   date=2026-07-15  status=SKIPPED    trigger=internal_watchdog SKIPPED
```

**daily_pipeline_runs (stale rows found — data quality issue):**
```
id=74  date=2026-07-21  status=FAILED   candidates_seeded=5  candidates_failed=1
id=57  date=2026-07-20  status=FAILED   candidates_seeded=5  candidates_failed=5
id=20  date=2026-07-19  status=RUNNING  completed_at=NULL  (stale — never updated to COMPLETED)
id=15  date=2026-07-18  status=SCHEDULED (stale — never updated)
id=12  date=2026-07-17  status=RUNNING  (stale — never updated)
```
> **Data quality gap:** daily_pipeline_runs rows for 2026-07-17/18/19 are stuck in RUNNING/SCHEDULED state. These are stale from prior runs and indicate the `completed_at` update path has a bug.

---

## 7. SSE / WebSocket / Real-Time Inspection

**Command:** `grep -rn "text/event-stream" artifacts/stock-scanner-api/main.py`
**Result:** line 66824 — `mimetype="text/event-stream"` — **1 SSE endpoint only**

**Command:** `grep -rn "WebSocket\|flask_socketio\|websockets" artifacts/stock-scanner-api/*.py`
**Result:** 0 matches — no WebSocket anywhere

**Command:** `grep -rn "EventSource" artifacts/stock-scanner/src/`
**Result:** 0 matches — frontend does not consume SSE

**Frontend polling:** No explicit polling loop found in React frontend. Dashboard must implement its own polling.

---

## 8. Trace-ID Queries

**Command:**
```sql
SELECT table_name FROM information_schema.columns
WHERE table_schema='public' AND column_name='trace_id' ORDER BY table_name;
```
**Result (34 tables):**
aiem_bus_transfer_log, aiem_d2_subcheck_log, aiem_diagram2_trace_audit, aiem_execution_assessments, aiem_pipeline_audit_log, aiem_pipeline_proof_log, aiem_specialist_council_runs, aiem_trade_attribution, ape_gate_decisions, ape_portfolio_snapshots, bull_bear_debates, d3_governance_acks, d3_governance_decisions, d3_governance_requests, daily_pipeline_runs, feedback_failure_log, oe_counterfactual_outcomes, oe_counterfactual_snapshots, oe_decision_records, oe_gate_events, oe_indicator_snapshots, oe_knowledge_base, oe_legacy_replay_exceptions, oe_no_trade_candidates, oe_options_metrics, oe_pattern_snapshots, oe_portfolio_context, oe_root_cause_records, oe_scheduler_trace, oe_strategy_candidates, oe_trade_records, options_engine_runs, options_pipeline_jobs, scheduler_run_audit

**Command:**
```sql
SELECT table_name FROM information_schema.columns
WHERE table_schema='public' AND column_name='audit_trace_id' ORDER BY table_name;
```
**Result (17 tables):**
aiem_candidate_rankings, aiem_decision_log, aiem_paper_thompson_history, aiem_paper_thompson_history_backup_20260709, aiem_paper_trades, aiem_paper_trades_backup_20260709, aiem_supervisor_bad_learning_flags, aiem_supervisor_event_log, aiem_supervisor_learning_review, aiem_supervisor_loop_audit, aiem_supervisor_overfit_checks, aiem_supervisor_overrides, aiem_supervisor_risk_checks, aiem_supervisor_risk_review, signal_trust_history, signal_trust_history_backup_20260709, telegram_alert_ledger

**Key correction:** In-memory variable `_d2_trace_id` is stored to DB as `audit_trace_id` — no column named `d2_trace_id` exists anywhere.

---

## 9. Hash-Chain Evidence

**Command:** `tail -10 artifacts/stock-scanner-api/evidence_chain.log`

**Result (current state, SEQ 1-10):**
```
{"seq":1, "timestamp_utc":"2026-07-18T08:53:18.820667Z", "command":"python portfolio_engine_verify.py --section ALL", "exit_code":0, ...}
{"seq":2, "timestamp_utc":"2026-07-21T00:41:14.487478Z", "command":"python3 dpl/verify_dpl_phase3.py", "exit_code":1, ...}
...
{"seq":10,"timestamp_utc":"2026-07-21T04:42:15.858101Z", "command":"cd artifacts/stock-scanner-api && python3 dpl/verify_dpl_phase3.py", "exit_code":1, ...}
```

**Correction:** SEQ=10 (not 61 as stated in memory — memory entry was stale/incorrect).
**Note:** Sequences 2-10 all show `exit_code:1` for `verify_dpl_phase3.py` — DPL Phase 3 verifier is currently failing.

---

## 10. Git Metadata

**Command:** `git --no-optional-locks log --oneline -3`
**Result:**
```
9f1f406 (HEAD -> main) Complete AIEM Dashboard Phase A inventory and reports
73d596a Add a new image to the attached assets for documentation
327a02c Document scheduler freeze and live-fire verification protocol
```

**Command:** `git --no-optional-locks status --short`
**Result:**
```
?? attached_assets/Pasted--AEIM-DASHBOARD-PHASE-A-RESULTS-HANDOF-1784649711660_1784649711661.txt
```
(No uncommitted changes to tracked files — Phase A files are committed)

---

## 11. SHA-256 Manifest (Phase A Files)

```
df3710e809b5fef2511d4ba721dc298f7402acce30222c9f9e83005b57426fb7  AIEM_DASHBOARD_CODE_INVENTORY.md
49637f503c5fca523ae353df902d157393ade5722e0db867c4f9e7ce5297aff4  AIEM_DASHBOARD_DATABASE_INVENTORY.md
8b1c27ddbbcfbdcbd38a3e4d85a9fd5da48f34f2c9c5b72d3b16d1b37ecc0528  AIEM_DASHBOARD_API_INVENTORY.md
9844d7c05a672e5a084c58a880de93cb825ed1b8261860d52135a79d4d0c9fe0  AIEM_DASHBOARD_REALTIME_INVENTORY.md
228d9b7d22b19688165ea2cfec0e003081603f624ac238428c15a8860563c38d  AIEM_DASHBOARD_SOURCE_TO_SCREEN_MAP.csv
949650104aca2dcde910210f53caa9dfaefc85975b544bdccd219974adb8983e  AIEM_DASHBOARD_TRACEABILITY_REPORT.md
53ae7b3f5709cca8fe2fcf3eee252d8f8d5392c07f9bcbf54c385e2fef904f85  AIEM_DASHBOARD_GAP_ANALYSIS.md
7d0d16de22abcb3e58276476f90cc97d5d0717f7bc4b058f5425d0d2f24e8593  AIEM_DASHBOARD_PHASE_A_FINAL_REPORT.md
80a1ed62692dd9be8f643ce0beff6eddd0f018365259c60947d67034ef5a4fb2  AIEM_DASHBOARD_PHONE_SUMMARY.md
```

**Generation timestamp:** 2026-07-21 (ET)
**Manually edited after generation:** NO — all files were written in a single generation pass and committed immediately.

---

## 12. Database Quality Raw Stats

```
TOTAL_TABLES:                 364
EMPTY_TABLES (0 rows):        155
NON-EMPTY TABLES:             209
TABLES_WITHOUT_PK:              6
TABLES_WITHOUT_TIMESTAMP:       1
TABLES_WITH_IS_TEST_RECORD:    23  (column exists — 0 currently have test rows)
OPTIONS_ENGINE_OE_TABLES:      45  (31 with rows, 14 empty)
AIEM_AIEM_TABLES:             110
D3_GOVERNANCE_TABLES:          25
BACKUP_TABLES:                  6
TABLES_WITHOUT_ANY_INDEX:       6
```
