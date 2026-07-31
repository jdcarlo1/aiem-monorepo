# Options Engine Dashboard — API Inventory
**Generated:** 2026-07-30 | **Source:** grep -n "@app.route" main.py

---

## Auth Model

```
Header:  X-Admin-Token: <value>
Secret:  ADMIN_TOKEN environment variable
Check:   hmac.compare_digest() via _admin_ok() helper (main.py line 231/253)
Failure: HTTP 401 {"error": "unauthorized"}
```

---

## Existing Routes (OE Dashboard-relevant)

### Screen: Live Decisions

| Method | Route | Line | Auth | DB / Source | Notes |
|--------|-------|------|------|-------------|-------|
| GET | /stock-api/options-pipeline/candidates | 12527 | ADMIN | aiem_options_alerts LEFT JOIN oe_decision_audit | alert_date param, direction filter, min_score, limit/offset |
| GET | /stock-api/admin/pipeline-checkpoint | 24602 | ADMIN | options_pipeline_jobs | checkpoint status per scan_date |
| GET | /stock-api/admin/decision-audit | 71886 | ADMIN | oe_decision_audit | date, ticker, limit params; is_test_record=FALSE filter required |
| GET | /stock-api/admin/gate-events | 71963 | ADMIN | oe_gate_events | date, ticker, limit |

**SSE: NONE for options pipeline. Must build. See Gap G-P0-SSE.**

---

### Screen: Decision Proof

| Method | Route | Line | Auth | DB / Source | Notes |
|--------|-------|------|------|-------------|-------|
| GET | /stock-api/admin/decision-audit | 71886 | ADMIN | oe_decision_audit | 6 JSONB layers per row (identity/technical/options_intel/probability_risk/justification) |
| GET | /stock-api/admin/gate-events | 71963 | ADMIN | oe_gate_events | chain_hash, prev_hash, event_hash per gate event |
| GET | /stock-api/admin/evidence-chain/status | 72180 | ADMIN | evidence_chain.log file | Returns last N entries + chain integrity |

---

### Screen: Positions & P&L

| Method | Route | Line | Auth | DB / Source | Notes |
|--------|-------|------|------|-------------|-------|
| GET | /stock-api/aiem-paper-portfolio | 49762 | None | aiem_paper_trades | Includes direction, strike, expiry, probability_score |
| GET | /stock-api/admin/position-sizing-log | 72104 | ADMIN | aiem_position_sizing_log | conviction_score, gate_result per ticker |
| ~~GET~~ | ~~(missing)~~ | — | — | oe_options_metrics | **NO ROUTE** — greeks/EV/POP per decision |
| ~~GET~~ | ~~(missing)~~ | — | — | oe_trade_records | **NO ROUTE** — closed options trades P&L |

---

### Screen: Calibration

| Method | Route | Line | Auth | DB / Source | Notes |
|--------|-------|------|------|-------------|-------|
| GET | /stock-api/aiem-probability-engine/calibration | 50884 | None | aiem_probability_engine_predictions + calibrated_horizon_{1-4}d.pkl | Calls pit_metrics.run_pit_metrics() — real DB read + sklearn |
| GET | /stock-api/aiem-probability-engine/daily-picks | 50667 | None | aiem_probability_engine_daily_picks | rank, prob_up_1d/2d/3d/4d per pick |
| GET | /stock-api/aiem-probability-engine/track-record | 50727 | None | aiem_probability_engine_predictions | Outcome resolution for past predictions |
| POST | /stock-api/aiem-probability-engine/live-query | 51034 | None | aiem_probability_engine_live_queries | Real-time probability query |

---

### Screen: System Status

| Method | Route | Line | Auth | DB / Source | Notes |
|--------|-------|------|------|-------------|-------|
| GET | /stock-api/health | 150 | None | — | Basic liveness |
| GET | /stock-api/healthz | ~62564 | None | — | Extended health |
| GET | /stock-api/admin/job-health | ~58485 | ADMIN | job_heartbeats | All jobs: last_success, consecutive_failures |
| GET | /stock-api/admin/job-heartbeats | ~58495 | ADMIN | job_heartbeats | Full heartbeat detail |
| GET | /stock-api/admin/scheduler-jobs | ~68917 | ADMIN | APScheduler in-memory | Live scheduler state |
| GET | /stock-api/options/reconcile | 1879 | None | aiem_options_alerts | DB count + recent rows for reconciliation |

---

### Supplemental (useful for multiple screens)

| Method | Route | Line | Auth | DB / Source |
|--------|-------|------|------|-------------|
| GET | /stock-api/quant/options-probability | 1855 | None | Live Tradier chain | Manual options probability calculator |
| POST | /stock-api/admin/options/run-seed | 12505 | ADMIN | Proxies → localhost:5053/run-seed | Trigger seed_daily_candidates() manually |

---

## Missing Routes (must add before Phase B screens can build)

| Priority | Route to Add | Table | Screen | Effort |
|----------|-------------|-------|--------|--------|
| P0 | SSE: GET /stock-api/admin/options-pipeline/stream | aiem_communication_bus → aiem_bus_transfer_log | Live Decisions | Medium |
| P1 | GET /stock-api/admin/options-metrics?trace_id=&date= | oe_options_metrics | Positions & P&L, "Why this trade" | Small |
| P1 | GET /stock-api/admin/trade-records?date=&limit= | oe_trade_records | Positions & P&L | Small |
| P1 | GET /stock-api/admin/indicator-snapshots?trace_id= | oe_indicator_snapshots | "Why this trade" panel | Small |
| P2 | GET /stock-api/admin/scheduler-trace?date= | oe_scheduler_trace | System Status | Tiny |

---

## Routes Not Suitable for OE Dashboard (internal/trigger-only)

| Route | Reason |
|-------|--------|
| POST /stock-api/admin/options/run-seed | Trigger-only — never expose as UI button |
| POST /stock-api/aiem-probability-engine/force-run | Trigger-only |
| POST /stock-api/aiem-paper-portfolio/force-execute | Paper trade execution — internal |
| POST /stock-api/aiem-paper-portfolio/force-mtm | MTM trigger — internal |
