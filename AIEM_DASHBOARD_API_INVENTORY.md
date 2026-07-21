# AIEM DASHBOARD — PHASE A
## API Inventory
**Generated:** 2026-07-21 | **Source:** artifacts/stock-scanner-api/main.py | **Total routes:** 333

---

## Authentication
All `/stock-api/admin/*` routes check the `ADMIN_TOKEN` environment secret via request header.  
Public routes (`/stock-api/paper-trades`, `/stock-api/aiem-paper-portfolio`, etc.) have no auth.  
AIEM chat routes use a separate HMAC signing system (`aiem_security.py`).

---

## Dashboard-Relevant Routes by Screen

### Command Center

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/health | 150 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/healthz | 62564 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/macro/latest | 19162 | ADMIN | macro tables | READY_FOR_DASHBOARD |
| GET | /stock-api/market/overview | 50320 | None | multiple | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/regime-overlay-check | 45056 | ADMIN | regime_history | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/job-health | 58485 | ADMIN | job_heartbeats | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/job-heartbeats | 58495 | ADMIN | job_heartbeats | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/scheduler-jobs | 68917 | ADMIN | — | READY_FOR_DASHBOARD |

### Live Decisions

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/aiem-paper-portfolio | 47580 | None | aiem_paper_trades | READY_FOR_DASHBOARD |
| GET | /stock-api/paper-trades | 47579 | None | aiem_paper_trades | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/aiem-pipeline-audit | 47892 | ADMIN | aiem_pipeline_audit_log | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/aiem-pipeline-audit/\<trace_id\> | 47917 | ADMIN | aiem_pipeline_audit_log | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/aiem-pipeline-audit/learning-loop | 47932 | ADMIN | aiem_closed_loop_learning | READY_FOR_DASHBOARD |
| POST | /stock-api/admin/aiem-pipeline-audit/run-verification | 47946 | ADMIN | — | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/pipeline-checkpoint | 22609 | ADMIN | options_pipeline_jobs | READY_FOR_DASHBOARD |
| POST | /stock-api/aiem-paper-portfolio/force-execute | 47769 | None | aiem_paper_trades | INTERNAL_ONLY |
| GET | /stock-api/aiem-paper-portfolio/execution-log | 47789 | None | aiem_paper_execution_log | READY_FOR_DASHBOARD |

### Opportunity Queue

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/nano-morning/picks | 11340 | None | aiem_process_predictions | READY_FOR_DASHBOARD |
| GET | /stock-api/nano-morning/candidates | 11232 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/sc-morning/picks | 12706 | None | sc_morning_picks | READY_FOR_DASHBOARD |
| GET | /stock-api/gap-volume-signal | 63383 | None | polygon_rvol_scan | READY_FOR_DASHBOARD |
| GET | /stock-api/full-market-movers | 63371 | None | polygon_market_daily | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem-predictions | 54711 | None | aiem_process_predictions | READY_FOR_DASHBOARD |
| GET | /stock-api/premarket | 50988 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/morning-runners | 60025 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/multiday-runners | 64076 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/washout-ignition-signal | 64772 | None | washout_ignition_signal | READY_FOR_DASHBOARD |
| GET | /stock-api/pullback-reentry | 64830 | None | aiem_pullback_signals | READY_FOR_DASHBOARD |
| GET | /stock-api/momentum-exhaustion | 64936 | None | — | READY_FOR_DASHBOARD |

### Decision Proof

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/admin/aiem-signed-proof | 22294 | ADMIN | aiem_verification_log | READY_FOR_DASHBOARD |
| POST | /stock-api/admin/aiem-verify-proof | 22321 | ADMIN | aiem_verification_log | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/verify/\<job_id\> | 66904 | HMAC | aiem_verify_link_tokens | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/verify-link/\<job_id\> | 67098 | None | aiem_verify_link_tokens | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/closed-loop-audit/\<trade_id\> | 48093 | ADMIN | aiem_supervisor_loop_audit | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/closed-loop-summary | 48115 | ADMIN | aiem_supervisor_loop_audit | READY_FOR_DASHBOARD |

### Paper Trading

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/aiem-paper-portfolio | 47580 | None | aiem_paper_trades | READY_FOR_DASHBOARD |
| GET | /stock-api/paper-trades | 47579 | None | aiem_paper_trades | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem-paper-portfolio/execution-log | 47789 | None | aiem_paper_execution_log | READY_FOR_DASHBOARD |
| POST | /stock-api/admin/paper-trade/\<trade_id\>/close | 48037 | ADMIN | aiem_paper_trades | INTERNAL_ONLY |
| POST | /stock-api/aiem-paper-portfolio/force-mtm | 47818 | None | aiem_paper_trades | INTERNAL_ONLY |
| GET | /stock-api/admin/paper-fill-audit | 47829 | ADMIN | aiem_paper_execution_log | READY_FOR_DASHBOARD |
| POST | /stock-api/admin/run-paper-today | 18958 | ADMIN | paper_trade_job_ledger | INTERNAL_ONLY |

### Portfolio Risk

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/portfolio | 45431 | None | portfolio (in-memory) | NEEDS_EXTENSION |
| GET | /stock-api/admin/aiem-v3/verify | 19195 | ADMIN | aiem_verification_log | READY_FOR_DASHBOARD |
| GET | /stock-api/gamma-pressure | 54152 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/gamma-wall | 51281 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/charm-cascade | 54450 | None | — | READY_FOR_DASHBOARD |

### Specialist Council

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| POST | /stock-api/admin/run-council-now | 65213 | ADMIN | aiem_specialist_council_runs | INTERNAL_ONLY |
| GET | /stock-api/admin/supervisor-summary | 47964 | ADMIN | aiem_supervisor_event_log | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/supervisor-daily-report | 47977 | ADMIN | aiem_supervisor_event_log | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/supervisor-weekly-report | 47990 | ADMIN | aiem_supervisor_event_log | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/supervisor-signal-lifecycle | 48003 | ADMIN | aiem_supervisor_event_log | NEEDS_EXTENSION |
| GET | /stock-api/admin/supervisor-overfit-check | 48020 | ADMIN | — | READY_FOR_DASHBOARD |

### Indicator Laboratory

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/quant/options-probability | 1626 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/candlestick-confluence | 32149 | None | candlestick_confluence_signals | READY_FOR_DASHBOARD |
| GET | /stock-api/iv-rank | 59427 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/iv-rank/scan | 59427 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/flow-scores | 15830 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/stat-arb/signals | 65830 | None | stat_arb_signals | READY_FOR_DASHBOARD |
| GET | /stock-api/nan-quant/latest | 11233 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/conviction-stack | 54245 | None | conviction_stack_watchlist | READY_FOR_DASHBOARD |
| GET | /stock-api/conviction-stack/score/\<ticker\> | 54392 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/raw-technicals/\<ticker\> | 54913 | ADMIN | polygon_market_daily | READY_FOR_DASHBOARD |

### Probability & Calibration

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/aiem-probability-engine/daily-picks | 48317 | None | aiem_probability_engine_daily_picks | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem-probability-engine/track-record | 48377 | None | aiem_probability_engine_predictions | READY_FOR_DASHBOARD |
| POST | /stock-api/aiem-probability-engine/live-query | 48563 | None | aiem_probability_engine_live_queries | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem-probability-engine/live-query/verify/\<row_id\> | 48621 | None | aiem_probability_engine_live_queries | READY_FOR_DASHBOARD |
| POST | /stock-api/aiem-probability-engine/force-run | 48534 | None | — | INTERNAL_ONLY |

### Performance Analytics

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/outcomes | 50729 | None | signal_outcomes | READY_FOR_DASHBOARD |
| GET | /stock-api/ai-trade-log | 53092 | None | aiem_paper_trades | READY_FOR_DASHBOARD |
| GET | /stock-api/eod-sweep-track-record | 55594 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/conviction-track-record | 54438 | None | conviction_calls_outcomes | READY_FOR_DASHBOARD |
| GET | /stock-api/runner-outcomes | 64085 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/discovery-outcomes | 48272 | ADMIN | aiem_signal_discoveries | READY_FOR_DASHBOARD |
| GET | /stock-api/analytics/historical | 48670 | None | polygon_market_daily | READY_FOR_DASHBOARD |

### Learning Center

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/admin/learning-proposals | 65940 | ADMIN | d3_learning_approvals | READY_FOR_DASHBOARD |
| POST | /stock-api/admin/learning-proposals/\<id\>/approve | 65972 | ADMIN | d3_learning_approvals | INTERNAL_ONLY |
| GET | /stock-api/admin/module2-status (via aiem/module2-status) | 65520 | None | aiem_module2_evaluations | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/module3-status | 65586 | None | aiem_module3_evaluations | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/module5-status | 65634 | None | aiem_module5_runs | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/discoveries | 63923 | None | aiem_signal_discoveries | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/model/history | 58526 | ADMIN | retrain_runs | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/model/current | 58533 | ADMIN | retrain_runs | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/signal-bridge/performance | 58562 | ADMIN | — | READY_FOR_DASHBOARD |

### Research & Hypotheses

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/aiem-research-status | 63958 | None | aiem_research_audit_sessions | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/run-discovery-outcome-check | 48197 | ADMIN | aiem_signal_discoveries | INTERNAL_ONLY |
| GET | /stock-api/admin/test-retest-adapter | 48213 | ADMIN | — | INTERNAL_ONLY |
| GET | /stock-api/admin/discovery-cycle/status | 55257 | ADMIN | — | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/discovery-cycle/report | 55500 | ADMIN | aiem_signal_discoveries | READY_FOR_DASHBOARD |

### Audit & Verification

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/admin/aiem-signed-proof | 22294 | ADMIN | aiem_verification_log | READY_FOR_DASHBOARD |
| POST | /stock-api/admin/aiem-verify-proof | 22321 | ADMIN | aiem_verification_log | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/verify/\<job_id\> | 66904 | HMAC | aiem_verify_link_tokens | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/verification/challenge | 67492 | None | — | READY_FOR_DASHBOARD |
| POST | /stock-api/aiem/verification/verify | 67516 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/aiem-pipeline-audit | 47892 | ADMIN | aiem_pipeline_audit_log | READY_FOR_DASHBOARD |

### System Operations

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/health | 150 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/healthz | 62564 | None | — | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/job-health | 58485 | ADMIN | job_heartbeats | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/job-heartbeats | 58495 | ADMIN | job_heartbeats | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/scheduler-jobs | 68917 | ADMIN | APScheduler in-memory | READY_FOR_DASHBOARD |
| GET | /stock-api/admin/aiem-process/last-scan-status | 11526 | ADMIN | aiem_process_predictions | READY_FOR_DASHBOARD |
| GET | /stock-api/__debug/threads | 67540 | None | — | INTERNAL_ONLY |
| GET | /stock-api/admin/macro/latest | 19162 | ADMIN | macro tables | READY_FOR_DASHBOARD |

### AIEM Chat / AI Interface

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| POST | /stock-api/aiem/chat | 66352 | HMAC | quant_agent_sessions | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/chat/stream | 66690 | HMAC | quant_agent_sessions | READY_FOR_DASHBOARD (SSE) |
| GET | /stock-api/aiem/chat/\<job_id\> | 66830 | None | quant_agent_sessions | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/history | 67267 | None | quant_agent_sessions | READY_FOR_DASHBOARD |
| GET | /stock-api/aiem/chat/history | 67352 | None | quant_agent_sessions | READY_FOR_DASHBOARD |

### Administration

| Method | Route | Line | Auth | DB Source | Status |
|--------|-------|------|------|-----------|--------|
| GET | /stock-api/admin/module4-pending | 65715 | ADMIN | d3_governance_requests | READY_FOR_DASHBOARD |
| POST | /stock-api/admin/module4-approve | 65739 | ADMIN | d3_governance_decisions | INTERNAL_ONLY |
| GET | /stock-api/admin/module4-history | 65808 | ADMIN | d3_governance_decisions | READY_FOR_DASHBOARD |
| GET/POST | /stock-api/user/prefs | 67649/67702 | None | subscriber_preferences | READY_FOR_DASHBOARD |
| GET/POST | /stock-api/user/watchlist | 67750 | None | subscriber_watchlist | READY_FOR_DASHBOARD |

---

## Routes Needing Extension for Dashboard

| Route | Missing Capability | Work Required |
|-------|--------------------|---------------|
| /stock-api/portfolio | Portfolio greeks not stored — only in-memory | Add DB persistence for ape_portfolio_greeks |
| /stock-api/aiem-paper-portfolio | No pagination, no date filter | Add ?date= and pagination params |
| Any route | No WebSocket/real-time push | See Real-time Inventory |
| (missing) | No route for oe_decision_audit browser | Need GET /stock-api/admin/decision-audit endpoint |
| (missing) | No route for oe_gate_events list | Need GET /stock-api/admin/gate-events endpoint |
| (missing) | No route for specialist council runs list | Need GET /stock-api/admin/council-runs endpoint |

---

## Route Classification Summary

| Classification | Count |
|----------------|-------|
| READY_FOR_DASHBOARD | ~180 |
| INTERNAL_ONLY (admin triggers, force-run) | ~60 |
| NEEDS_EXTENSION | ~8 |
| MISSING (needed, not yet built) | ~5 |
| BROKEN | 0 confirmed |
| INSECURE | 0 confirmed (admin routes all check ADMIN_TOKEN) |
