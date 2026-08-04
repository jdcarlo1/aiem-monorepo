---
name: Options Engine Phase 6 — Entry Trigger Engine
description: aiem_options_trigger_engine.py architecture, status, and Phase 7 wiring gap
---

## Status
Committed `59dbc44` — PASS gate (TLA-64b50fe6, self_issued with PHASE-6-ENTRY-TRIGGER-ENGINE directive).

## What exists
- `artifacts/stock-scanner-api/aiem_options_trigger_engine.py` — standalone module
- `oe_execution_plans` table (bootstrapped on scheduler start)
- 11 trigger types: VWAP_RECLAIM, VWAP_REJECTION, SR_BREAK, PM_HIGH_BREAK, PM_LOW_BREAK,
  ORB_BREAK, PULLBACK_CONFIRMED, VOLUME_CONFIRM, MOMENTUM_CONFIRM, SECTOR_ALIGN, LIQUIDITY_CONFIRM
- 8 pre-fill revalidation checks (all must pass): spot_freshness, chain_freshness, liquidity,
  trigger_validity, duplicate_protection, portfolio_limits, max_loss, expected_value
- Scheduler: 5-min `trigger_plan_check` job (expire + evaluate pending plans)
- bootstrap hook in `_bootstrap_db()` at scheduler startup

## Phase 6 scope (paper only)
Plans are created alongside the immediate fill path in `_execute_job` — not wired to replace it.
The direct fill still calls `save_options_alert()` / `capture_trade_record()`.
The execution plan lifecycle (PENDING→TRIGGER_MET→FILLED/CANCELLED) is self-contained.

## Phase 7 gap (not implemented)
`check_all_pending_plans(snapshot_fn, ...)` — `snapshot_fn` currently returns `{}` (empty dict).
Phase 7 must wire a real Polygon/Tradier live price fetcher as `snapshot_fn` so trigger
conditions evaluate against real market data instead of just expiring.

## Evidence produced
- Item 16: NVDA LONG_CALL `oep_abb85cb585a240939ee1`
  plan_created_at=2026-08-04T01:17:24Z → trigger_met_at=2026-08-04T01:17:26Z (2.09s gap)
- Item 17: META LONG_PUT `oep_3252d60ed3654e718115`
  TRIGGER_MET → CANCELLED (prefill_revalidation_failed: spot_freshness, chain_freshness)
  quote_age_seconds=9999 → both freshness checks FAIL

## oe_execution_plans schema
Primary key: plan_id (TEXT, uuid-hex prefix "oep_")
Status states: PENDING_EXECUTION_PLAN → TRIGGER_MET → FILLED | CANCELLED | TRIGGER_EXPIRED
Key cols: trigger_type, trigger_condition (JSONB), revalidation_json (JSONB),
          cancel_reason, fill_ts, fill_alert_id, is_test_record

**Why:** Phase 6 creates plans but doesn't replace immediate fills. Full replacement
(strategy-selection creates plan, trigger fires, then fill) is Phase 7+ scope.
