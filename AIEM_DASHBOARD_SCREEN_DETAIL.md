# AIEM DASHBOARD — PHASE A
## 16 Planned Screens — Exact Detail
**Generated:** 2026-07-21 | Note: All screens are AIEM-product only; Stock Scanner screens excluded

---

## Screen Status Key
- **READY_NOW** — all routes exist, all data populated, auth wired, build can start
- **READY_AFTER_API_CONNECTION** — routes exist but not yet wired to any frontend
- **REQUIRES_SMALL_BACKEND_ROUTE** — missing 1-2 simple read-only routes (< 4h work each)
- **REQUIRES_BACKEND_REMEDIATION** — backend data quality or persistence gap blocks this screen
- **FUTURE_FEATURE** — requires significant new backend capability
- **NOT_SUPPORTED** — no backend data exists

---

## Screen 1: Command Center

**Status:** READY_NOW  
**Description:** Market regime, macro score, active job status, system health at a glance

| Item | Detail |
|------|--------|
| Supported widgets | Regime label (BULL/BEAR/NEUTRAL), macro score (0-100), job health grid, scheduler next-fire times, heartbeat liveness |
| Existing routes | `GET /stock-api/market/overview`, `GET /stock-api/admin/macro/latest`, `GET /stock-api/admin/scheduler-jobs`, `GET /stock-api/admin/job-heartbeats`, `GET /stock-api/health` |
| Missing routes | None |
| DB sources | `regime_history`, `paper_trade_watchdog_heartbeat`, APScheduler in-memory |
| Refresh method | Poll 30s |
| Auth level | Mixed — market/health: none; macro/scheduler: ADMIN (X-Admin-Token) |
| Known limitations | Macro score cached — updates 9AM ET only; scheduler shows 274 jobs total, dashboard should filter to AIEM-relevant ~20 |

---

## Screen 2: Live Decisions

**Status:** REQUIRES_SMALL_BACKEND_ROUTE  
**Description:** Current options pipeline job state, decision audit trail, gate events

| Item | Detail |
|------|--------|
| Supported widgets | Pipeline job list by date, job status (PENDING/EXECUTING/COMPLETED/FAILED), gate events fired, decision audit hash |
| Existing routes | `GET /stock-api/admin/pipeline-checkpoint`, `GET /stock-api/admin/aiem-pipeline-audit`, `GET /stock-api/admin/aiem-pipeline-audit/<trace_id>` |
| Missing routes | `GET /stock-api/admin/decision-audit` (oe_decision_audit — 341 rows, HIGH PRIORITY), `GET /stock-api/admin/gate-events` (oe_gate_events — 4 rows) |
| DB sources | `options_pipeline_jobs`, `oe_decision_audit`, `oe_gate_events`, `aiem_pipeline_audit_log` |
| Refresh method | Poll 30s for job status; on-demand for audit detail |
| Auth level | ADMIN (X-Admin-Token) |
| Known limitations | daily_pipeline_runs has stale RUNNING rows from 2026-07-17/18/19 — display logic must filter by status=COMPLETED only |

---

## Screen 3: Opportunity Queue

**Status:** READY_NOW  
**Description:** Today's ranked candidates from all AIEM signal sources

| Item | Detail |
|------|--------|
| Supported widgets | AIEM Independent Picks table (rank/ticker/confidence/signal_basis), Gap+Volume signal grid, polygon RVOL scan, washout ignition, pullback re-entry |
| Existing routes | `GET /stock-api/aiem-predictions`, `GET /stock-api/gap-volume-signal`, `GET /stock-api/washout-ignition-signal`, `GET /stock-api/pullback-reentry`, `GET /stock-api/momentum-exhaustion`, `GET /stock-api/full-market-movers` |
| Missing routes | None |
| DB sources | `aiem_process_predictions` (60 rows), `polygon_rvol_scan` (110 rows), `washout_ignition_signal`, `aiem_pullback_signals` |
| Refresh method | Poll 60s — data updates at 9:30/9:35 ET only; stale indicator on screen after 10AM |
| Auth level | None |
| Known limitations | `aiem_process_predictions` updated by aiem-process workflow only; if that workflow is down, data goes stale |

---

## Screen 4: Decision Proof

**Status:** REQUIRES_SMALL_BACKEND_ROUTE  
**Description:** Cryptographic audit trail for any AIEM decision

| Item | Detail |
|------|--------|
| Supported widgets | Signed proof link (HMAC), evidence chain current state (SEQ/hash), verify-link QR/URL for any session, closed-loop audit by trade |
| Existing routes | `GET /stock-api/admin/aiem-signed-proof`, `POST /stock-api/admin/aiem-verify-proof`, `GET /stock-api/aiem/verify-link/<job_id>`, `GET /stock-api/admin/closed-loop-summary` |
| Missing routes | `GET /stock-api/admin/evidence-chain/status` (reads evidence_chain.log, returns JSON with SEQ=10 and last hash) |
| DB sources | `aiem_verification_log` (358 rows), `evidence_chain.log` (SEQ=10), `aiem_supervisor_loop_audit` |
| Refresh method | On-demand for proof verification; poll 60s for chain SEQ |
| Auth level | ADMIN for proof generation; none for verify-link consumption |
| Known limitations | Evidence chain is a file, not DB — current SEQ=10, not 61 as memory previously stated; verify_dpl_phase3.py returns exit_code=1 on all recent runs |

---

## Screen 5: Paper Trading

**Status:** READY_NOW  
**Description:** AIEM autonomous paper trade portfolio — open positions, execution history, job run log

| Item | Detail |
|------|--------|
| Supported widgets | Open positions table (ticker/type/entry/last/P&L/status), execution log (fill details, slippage), job ledger (run history, picks_count) |
| Existing routes | `GET /stock-api/aiem-paper-portfolio`, `GET /stock-api/paper-trades`, `GET /stock-api/aiem-paper-portfolio/execution-log`, `GET /stock-api/admin/paper-fill-audit` |
| Missing routes | `GET /stock-api/admin/paper-job-ledger` (paper_trade_job_ledger — run history, 5 rows) |
| DB sources | `aiem_paper_trades` (31 rows, 2026-07-12 to 2026-07-21), `aiem_paper_execution_log` (20 rows), `paper_trade_job_ledger` (5 rows) |
| Refresh method | Poll 30s for open positions; poll 60s for execution log |
| Auth level | None for portfolio; ADMIN for fill-audit and job-ledger |
| Known limitations | No pagination — /aiem-paper-portfolio returns all 31 trades; add ?date= filter; `aiem_paper_trades` has 43 columns — dashboard should display only key subset |

---

## Screen 6: Portfolio Risk

**Status:** REQUIRES_BACKEND_REMEDIATION  
**Description:** Real-time risk metrics for open AIEM positions

| Item | Detail |
|------|--------|
| Supported widgets | Per-position sizing log (conviction/stop/notional), gamma/charm exposure, position sizing gate results |
| Existing routes | `GET /stock-api/gamma-wall`, `GET /stock-api/gamma-pressure`, `GET /stock-api/charm-cascade`, `GET /stock-api/portfolio` |
| Missing routes | `GET /stock-api/admin/position-sizing-log` (aiem_position_sizing_log — 207 rows, HIGH PRIORITY) |
| DB sources | `aiem_position_sizing_log` (207 rows, no API route), `gamma_wall_cache`, `gamma_pressure_cache` |
| Refresh method | Poll 60s |
| Auth level | None for gamma routes; ADMIN for sizing log |
| Known limitations | `/stock-api/portfolio` serves from in-memory dict — resets on restart; `aiem_position_sizing_log` has 207 rows of sizing decisions but no API route; ape_portfolio_snapshots (0 rows) not active |

---

## Screen 7: Specialist Council

**Status:** REQUIRES_SMALL_BACKEND_ROUTE  
**Description:** Council debate history, member opinions, weighted votes per trade decision

| Item | Detail |
|------|--------|
| Supported widgets | Council run list (ticker/context/weighted_vote/variance), member opinion breakdown (opinions JSONB), bull-bear debate history |
| Existing routes | `GET /stock-api/admin/supervisor-summary`, `GET /stock-api/admin/supervisor-daily-report` |
| Missing routes | `GET /stock-api/admin/council-runs` (aiem_specialist_council_runs — 219 rows, 13 columns including opinions JSONB) |
| DB sources | `aiem_specialist_council_runs` (219 rows, 2026-07-12 to 2026-07-21), `bull_bear_debates` (11 rows) |
| Refresh method | Poll 60s for new runs; on-demand for detail |
| Auth level | ADMIN |
| Known limitations | Council `opinions` column is JSONB — dashboard must parse to render member breakdown; `weighted_vote` is a float, not a label — display needs context |

---

## Screen 8: Indicator Laboratory

**Status:** REQUIRES_SMALL_BACKEND_ROUTE  
**Description:** Per-ticker indicator readings, candlestick patterns, behavioral matches, IV rank

| Item | Detail |
|------|--------|
| Supported widgets | Indicator snapshot grid (normalized scores by ticker), candlestick confluence signals, behavioral pattern matches, IV rank table |
| Existing routes | `GET /stock-api/candlestick-confluence`, `GET /stock-api/behavioral-matches`, `GET /stock-api/iv-rank`, `GET /stock-api/conviction-stack` |
| Missing routes | `GET /stock-api/admin/indicator-snapshots` (oe_indicator_snapshots — 1,739 rows with canonical_id/normalized_value) |
| DB sources | `oe_indicator_snapshots` (1,739 rows, AIEM-owned), `candlestick_confluence_signals` (269 rows), `behavioral_pattern_matches` (1,286 rows) |
| Refresh method | Poll 60s |
| Auth level | None for public screens; ADMIN for indicator-snapshots |
| Known limitations | oe_indicator_snapshots has 19 columns including `canonical_id` — dashboard must join to `oe_indicator_registry` (79 rows) to get human-readable names |

---

## Screen 9: Probability & Calibration

**Status:** READY_NOW  
**Description:** Probability engine daily picks, calibration track record, live query

| Item | Detail |
|------|--------|
| Supported widgets | Daily picks table (rank/ticker/prob_up_1d-4d/confidence), track record chart (predicted vs actual), live query form |
| Existing routes | `GET /stock-api/aiem-probability-engine/daily-picks`, `GET /stock-api/aiem-probability-engine/track-record`, `POST /stock-api/aiem-probability-engine/live-query`, `GET /stock-api/aiem-probability-engine/live-query/verify/<row_id>` |
| Missing routes | None |
| DB sources | `aiem_probability_engine_daily_picks` (10 rows), `aiem_probability_engine_predictions` (10 rows), `aiem_probability_engine_live_queries` (46 rows) |
| Refresh method | Poll 60s for picks (update at 9:45 ET + 15:45 ET); on-demand for live query |
| Auth level | None |
| Known limitations | Only 10 picks rows — track record chart may be sparse; probability engine is XGBoost-based, not LLM |

---

## Screen 10: Performance Analytics

**Status:** READY_NOW  
**Description:** AIEM paper trade outcomes, signal win rates, discovery outcome tracking

| Item | Detail |
|------|--------|
| Supported widgets | Closed trade P&L table, signal outcome win rates, discovery outcome grid, runner outcomes |
| Existing routes | `GET /stock-api/outcomes`, `GET /stock-api/ai-trade-log`, `GET /stock-api/eod-sweep-track-record`, `GET /stock-api/runner-outcomes`, `GET /stock-api/admin/discovery-outcomes` |
| Missing routes | None |
| DB sources | `signal_outcomes` (95 rows), `aiem_paper_trades` (closed trades via exit_date NOT NULL), `aiem_signal_discoveries` (5 rows) |
| Refresh method | Poll 60s; outcomes update EOD only |
| Auth level | None for public; ADMIN for discovery-outcomes |
| Known limitations | aiem_paper_trades has 31 total rows; closed trade count depends on exit_date NOT NULL filter |

---

## Screen 11: Learning Center

**Status:** READY_NOW  
**Description:** Signal discovery lifecycle, module status, ML retrain history, learning proposals

| Item | Detail |
|------|--------|
| Supported widgets | Signal discoveries table (5 rows), module 2/3/5 status badges, ML model history, learning proposals queue |
| Existing routes | `GET /stock-api/aiem/discoveries`, `GET /stock-api/aiem/module2-status`, `GET /stock-api/aiem/module3-status`, `GET /stock-api/aiem/module5-status`, `GET /stock-api/admin/model/history`, `GET /stock-api/admin/learning-proposals` |
| Missing routes | None |
| DB sources | `aiem_signal_discoveries` (5 rows), `aiem_module2_evaluations`, `aiem_module3_evaluations`, `retrain_runs` |
| Refresh method | Poll 60s for module status; on-demand for ML history |
| Auth level | None for module status/discoveries; ADMIN for model history and learning proposals |
| Known limitations | Only 5 validated signal discoveries — UI must handle sparse state gracefully |

---

## Screen 12: Research & Hypotheses

**Status:** READY_NOW  
**Description:** AIEM autonomous research sessions, discovery cycle status, hypotheses

| Item | Detail |
|------|--------|
| Supported widgets | Research session list (93 rows), discovery cycle status, signal discoveries with conditions |
| Existing routes | `GET /stock-api/aiem-research-status`, `GET /stock-api/admin/discovery-cycle/status`, `GET /stock-api/admin/discovery-cycle/report`, `GET /stock-api/aiem/discoveries` |
| Missing routes | None |
| DB sources | `aiem_research_audit_sessions` (93 rows), `aiem_signal_discoveries` (5 rows) |
| Refresh method | Poll 60s |
| Auth level | None for research-status; ADMIN for discovery-cycle |
| Known limitations | Research sessions may contain LLM-generated content — dashboard must sanitize before rendering |

---

## Screen 13: Audit & Verification

**Status:** REQUIRES_SMALL_BACKEND_ROUTE  
**Description:** Immutable audit trail: D3 governance, oe_decision_audit, evidence chain

| Item | Detail |
|------|--------|
| Supported widgets | Decision audit table (input_hash/output_hash/verification_status), gate events list, governance decisions timeline, evidence chain SEQ viewer |
| Existing routes | `GET /stock-api/admin/aiem-signed-proof`, `GET /stock-api/admin/aiem-pipeline-audit`, `GET /stock-api/admin/closed-loop-summary` |
| Missing routes | `GET /stock-api/admin/decision-audit` (341 rows — MOST CRITICAL), `GET /stock-api/admin/gate-events` (4 rows), `GET /stock-api/admin/governance-decisions`, `GET /stock-api/admin/evidence-chain/status` |
| DB sources | `oe_decision_audit` (341 rows), `oe_gate_events` (4 rows), `d3_governance_decisions` (94 rows), `evidence_chain.log` (SEQ=10) |
| Refresh method | Poll 60s for audit list; evidence chain poll 30s |
| Auth level | ADMIN |
| Known limitations | oe_decision_audit has `is_test_record` filter required; `evidence_chain.log` is a file — backend must serve its content via API |

---

## Screen 14: AIEM Chat

**Status:** READY_NOW (with polling fallback)  
**Description:** Interactive AIEM quant agent interface with session history and verify links

| Item | Detail |
|------|--------|
| Supported widgets | Chat input, streaming response (SSE), session history list, auto-minted verify link per session |
| Existing routes | `POST /stock-api/aiem/chat`, `GET /stock-api/aiem/chat/stream` (SSE), `GET /stock-api/aiem/chat/<job_id>`, `GET /stock-api/aiem/chat/history`, `GET /stock-api/aiem/verify-link/<job_id>` |
| Missing routes | None |
| DB sources | `quant_agent_sessions`, `aiem_verify_link_tokens` |
| Refresh method | SSE for streaming; poll 2s for job status fallback |
| Auth level | HMAC signing (aiem_security.py) |
| Known limitations | SSE endpoint exists but frontend currently has no EventSource implementation; polling fallback via `GET /stock-api/aiem/chat/<job_id>` works but has 2-4s latency per token batch |

---

## Screen 15: System Operations

**Status:** READY_NOW  
**Description:** All scheduler jobs, worker heartbeats, pipeline run history, daily pipeline run log

| Item | Detail |
|------|--------|
| Supported widgets | 274-job scheduler list (filtered to AIEM-relevant), heartbeat liveness grid, paper_trade_job_ledger history, daily_pipeline_runs table |
| Existing routes | `GET /stock-api/admin/scheduler-jobs` (274 jobs live), `GET /stock-api/admin/job-heartbeats`, `GET /stock-api/health`, `GET /stock-api/healthz` |
| Missing routes | `GET /stock-api/admin/daily-pipeline-runs` (daily_pipeline_runs — 6 rows), `GET /stock-api/admin/paper-job-ledger` (paper_trade_job_ledger — 5 rows) |
| DB sources | `paper_trade_watchdog_heartbeat` (2,321 rows), `paper_trade_job_ledger` (5 rows), `daily_pipeline_runs` (6 rows, some stale) |
| Refresh method | Poll 30s |
| Auth level | ADMIN |
| Known limitations | daily_pipeline_runs has stale RUNNING rows from 2026-07-17/18/19; display must filter or flag these |

---

## Screen 16: Administration

**Status:** READY_NOW  
**Description:** User preferences, API key management, module approval queue (Module 4)

| Item | Detail |
|------|--------|
| Supported widgets | User prefs form, watchlist editor, Module 4 pending approvals list, module history |
| Existing routes | `GET/POST /stock-api/user/prefs`, `GET/POST /stock-api/user/watchlist`, `GET /stock-api/admin/module4-pending`, `POST /stock-api/admin/module4-approve`, `GET /stock-api/admin/module4-history` |
| Missing routes | None |
| DB sources | `subscriber_preferences`, `subscriber_watchlist`, `d3_governance_requests` (9 rows) |
| Refresh method | On-demand |
| Auth level | None for user prefs; ADMIN for module4 |
| Known limitations | Module 4 approval is a governance action — must be gated behind confirmation dialog; no role separation between "read admin" and "write admin" |

---

## Screen Readiness Summary

| # | Screen | Status | Missing Work |
|---|--------|--------|-------------|
| 1 | Command Center | READY_NOW | — |
| 2 | Live Decisions | REQUIRES_SMALL_BACKEND_ROUTE | 2 routes |
| 3 | Opportunity Queue | READY_NOW | — |
| 4 | Decision Proof | REQUIRES_SMALL_BACKEND_ROUTE | 1 route |
| 5 | Paper Trading | READY_NOW | — |
| 6 | Portfolio Risk | REQUIRES_BACKEND_REMEDIATION | 1 route + in-memory issue |
| 7 | Specialist Council | REQUIRES_SMALL_BACKEND_ROUTE | 1 route |
| 8 | Indicator Laboratory | REQUIRES_SMALL_BACKEND_ROUTE | 1 route |
| 9 | Probability & Calibration | READY_NOW | — |
| 10 | Performance Analytics | READY_NOW | — |
| 11 | Learning Center | READY_NOW | — |
| 12 | Research & Hypotheses | READY_NOW | — |
| 13 | Audit & Verification | REQUIRES_SMALL_BACKEND_ROUTE | 4 routes |
| 14 | AIEM Chat | READY_NOW | — |
| 15 | System Operations | READY_NOW | — |
| 16 | Administration | READY_NOW | — |

**10 READY_NOW screens:** 1, 3, 5, 9, 10, 11, 12, 14, 15, 16  
**5 REQUIRES_SMALL_BACKEND_ROUTE:** 2, 4, 7, 8, 13  
**1 REQUIRES_BACKEND_REMEDIATION:** 6
