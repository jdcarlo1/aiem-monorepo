---
name: AIEM Dashboard Phase A Inventory
description: Complete read-only inventory of all AIEM engine outputs — modules, routes, tables, real-time infra, traceability, gaps. 9 output files at project root.
---

# AIEM Dashboard Phase A Inventory

**Completed:** 2026-07-21  
**Files:** 9 files at project root (AIEM_DASHBOARD_*.md + AIEM_DASHBOARD_SOURCE_TO_SCREEN_MAP.csv)

## Key Facts
- 239 Python modules scanned (all with SHA-256)
- 333 API routes in main.py
- 580 DB tables total; ~45 active+populated
- 1 SSE endpoint: `/stock-api/aiem/chat/stream` at main.py:66824 (chat only)
- 0 WebSocket, 0 Redis, 0 LISTEN/NOTIFY
- 7 scheduled jobs in options-pipeline-scheduler (APScheduler, CronTrigger)
- Hash chain: SEQ=61, evidence_chain.log (file, not DB table)

## Most Critical Missing Routes (Phase B prerequisite)
1. `GET /stock-api/admin/decision-audit` — oe_decision_audit (341 rows, cryptographic, NO ROUTE)
2. `GET /stock-api/admin/gate-events` — oe_gate_events (4 rows, NO ROUTE)
3. `GET /stock-api/admin/council-runs` — aiem_specialist_council_runs (219 rows, NO ROUTE)
4. `GET /stock-api/admin/position-sizing-log` — aiem_position_sizing_log (207 rows, NO ROUTE)
5. `GET /stock-api/admin/evidence-chain/status` — evidence_chain.log file read (NO ROUTE)

## Table: aiem_pipeline_audit_log
- Timestamp column is `logged_at` NOT `created_at` (confirmed — created_at does not exist)
- 284 rows, 2026-07-11 to 2026-07-21

## Table: oe_indicator_snapshots
- No `created_at` column (confirmed — does not exist)
- Use `signal_ts` or `data_ts` for timestamps

## Table: signal_fire_log
- Timestamp column is `fire_date` (date) NOT `fired_at`

## Table: aiem_signal_discoveries
- No `created_at` column

## Table: polygon_market_daily
- No `date` column — use `scan_date` or check actual column names

## Table: candlestick_confluence_signals
- No `signal_date` column

## Table: behavioral_pattern_matches
- Timestamp column is `scan_time` NOT `scan_date`

## Table: d3_governance_decisions
- No `created_at` column confirmed

## Traceability Chain Summary
- Paper trade: `_d2_trace_id` → links aiem_paper_trades → aiem_specialist_council_runs → aiem_supervisor_loop_audit → d3_governance_decisions
- Options pipeline: `trace_id` → links options_pipeline_jobs → oe_indicator_snapshots → oe_decision_audit → oe_gate_events → aiem_pipeline_audit_log
- Hash chain: `SEQ` counter in evidence_chain.log (file only, not DB)
- D3 governance: `execution_plan_id` links to paper_trade_id in d3_governance_event_links

## Phase B Build Order
1. Add 5 missing routes (small effort, no schema changes)
2. Create `artifacts/aiem-dashboard/` React+Vite app
3. ADMIN_TOKEN login UX
4. Polling infrastructure (10-30s per screen)
5. 16 screens by data completeness order (see GAP_ANALYSIS.md)

**Why:** Phase A was pure inventory — no code changes. All 9 files are production reference docs for Phase B build.
