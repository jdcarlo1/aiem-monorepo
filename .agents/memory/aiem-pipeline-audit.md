---
name: AIEM Pipeline Audit Layer
description: Per-trade pipeline trace proving AIEM (not scanner) is the decision-maker; 13-module PASS/FAIL; 4 admin endpoints
---

## What it does
Every paper trade gets a `audit_trace_id` stored in `aiem_paper_trades`. The trace
follows the trade through: signal received from scanner → AIEM intake → AIEM decision
→ outcome recorded → learning update applied.

## Key file
`artifacts/stock-scanner-api/aiem_pipeline_audit.py`

## DB table
`aiem_pipeline_audit_log` — 6 columns including source_system, processing_system,
decision_authority (always "AIEM" for final_aiem_decision step).

## 13 pipeline stages
1. signal_received (source_system=stock_scanner)
2. aiem_candidate_intake (processing_system=AIEM)
3-10. discovery cycle modules — marked VERIFIED_VIA_DISCOVERY_CYCLE if dc_log has recent run
11. final_aiem_decision (decision_authority=AIEM)
12. outcome_recorded (from MTM job)
13. learning_update_applied (from discovery cycle after Module 7)

## Admin endpoints
- GET  /stock-api/admin/aiem-pipeline-audit              (list traces)
- GET  /stock-api/admin/aiem-pipeline-audit/<trace_id>  (full report)
- GET  /stock-api/admin/aiem-pipeline-audit/learning-loop (8-stage loop check)
- POST /stock-api/admin/aiem-pipeline-audit/run-verification (live end-to-end)

## main.py wiring points
- `_init_aiem_paper_trades_table()` — ALTER TABLE audit_trace_id + DEFERRED_INIT
- `_aiem_paper_execute_today()` pick loop (after hold_days calc) — creates PipelineTrace, logs 3 steps, adds audit_trace_id to INSERT
- `_aiem_paper_mark_to_market()` — after EXIT UPDATE + print, queries audit_trace_id and calls log_outcome_for_trade()
- `_discovery_cycle_job()` — after _dc_module7_feedback_loop() calls log_learning_updates()

## psycopg2 rollback pattern
verify_closed_learning_loop() queries optional tables (signal_trust_weights, dc_template_feedback, etc.)
Each try/except must call _c.rollback() after catching an exception or subsequent
queries fail with "current transaction is aborted".

## Learning loop status (as of first deploy)
INCOMPLETE — 4 stages have 0 rows: dc_template_feedback, signal_trust_weights,
aiem_module3_evaluations, rl_experience_buffer. This is correct/honest — those
pipelines exist but haven't accumulated data yet.
