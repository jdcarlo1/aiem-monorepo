---
name: OE Dashboard Phase B — response shape normalisers
description: All 6 OE Dashboard pages and the actual backend response shapes, including new candidates route
---

# OE Dashboard Phase B — Response Shape Reference

## Key Design Rule
The stock-api admin routes return `{count, rows: [...], elapsed_ms}` dicts — not plain arrays.
Every OE Dashboard page component must call `extractRows<T>()` or an equivalent normaliser.

## Page → Route → Actual Response Shape

### Live Decisions (`/live-decisions`)
- `GET /admin/options-pipeline/candidates` → **plain array** (returns rows directly, not wrapped)
  - Fields: `id, ticker, scan_date, direction, status, trace_id, alert_id, selected_score, trigger_source, error_text, completed_at, gate_events_count, decision_id, verification_status`

### Decision Proof (`/decisions`)
- `GET /admin/decision-audit` → `{count, rows: [...], elapsed_ms, limit, offset}`
  - remap: `gate_event_id→id`, `gate_name→gate_type`, `fired_at→recorded_at` (in gate_events join)
  - remap: `seq→chain_seq` (evidence-chain)
  - now includes `probability_risk_json` and `justification_json` (fixed in backend)
- `GET /admin/gate-events` → `{count, rows: [...], elapsed_ms, limit}`
- `GET /admin/evidence-chain/status` → `{chain_path, last_command, last_entry_hash, last_exit_code, last_timestamp_utc, seq, total_entries}`

### Positions & P&L (`/positions`)
- `GET /admin/trade-records` → `{count, rows: [...], elapsed_ms, limit}`
- `GET /admin/options-metrics` → `{count, rows: [...], elapsed_ms, limit}`
- `GET /aiem-paper-portfolio` → `{positions: [...]}` or `{rows: [...]}` or plain array

### Why This Trade (`/why/:traceId`)
- `GET /admin/indicator-snapshots?trace_id=X` → `{count, rows: [...], elapsed_ms, limit, trace_id_queried}`
- `GET /admin/options-metrics?trace_id=X` → `{count, rows: [...], elapsed_ms, limit}`

### Calibration (`/calibration`)
- `GET /aiem-probability-engine/calibration` → `{calibrator_artifacts: {1d: {raw_brier_test_fold, cal_brier_test_fold, n_test, n_train, n_val, brier_improvement, method}, 2d: ..., 3d: ..., 4d: ...}, pit_metrics: {genuine: {n_rows_total, ...}, contaminated: {n_rows_total}, corrected: {n_rows_total}}, data_sources, note}`
- `GET /aiem-probability-engine/daily-picks` → `{pick_date: "YYYY-MM-DD", picks: [{ticker, prob_up_1d, prob_up_2d, prob_up_3d, prob_up_4d, confidence, score, regime_tag, warnings, top_contributing_layers, ...}]}`
- `GET /aiem-probability-engine/track-record` → `{note, pit_status_counts, rows: [{ticker, signal_date, prob_up_1d, ..., correct_1d, outcome_label_1d, ...}], summary, total_logged}`

### System Status (`/status`)
- `GET /admin/job-heartbeats` → `{jobs: [{job_name, last_success, last_attempt, consecutive_failures, last_error, ...}], status: "ok"}`
  - remap: `last_attempt→last_heartbeat`
  - NOTE: NOT `/admin/job-health` — that returns aggregate dict
- `GET /admin/scheduler-jobs` → `{job_count, jobs: [{id, name, next_run, trigger, ...}]}`
  - remap: `id→job_id`, `name→job_name`, `next_run→next_run_time`, `trigger→trigger_type`
- `GET /options/reconcile` → `{db_count, display_count, last_alert_date, last_created_at, reconcile_ok, sample}`
- `GET /admin/pipeline-checkpoint` → `{date, jobs: [{ticker, status}], pending, done, pipeline_run: {status, trigger_source}, needs_recovery}`

## Calibration Finding (2026-07-30)
Platt scaling DEGRADED on all 4 horizons — cal_brier > raw_brier. Raw Brier = honest metric.
Do NOT suppress DEGRADED banners.

## job_heartbeats columns (CONFIRMED)
`job_name, last_success, last_attempt, last_error, consecutive_failures`  
**No `status` column. No `recorded_at` column.**

## grade_outcomes in job_heartbeats
NOT wired to `record_job_success()` — no row exists for `job_name='grade_outcomes'`.  
`_job_ran_today()` fix: guard with `if row is None or row[0] is None: return True`.
