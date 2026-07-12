---
name: Order dedup enforcement — aiem_paper_trades
description: UNIQUE(ticker,trade_date) constraint + app pre-check in _aiem_paper_execute_today(); order_dedup.py is separate (order_execution_log only)
---

## Rule
`aiem_paper_trades` has a real `UNIQUE(ticker, trade_date)` DB constraint (name: `aiem_paper_trades_ticker_date_unique`). Before the INSERT in `_aiem_paper_execute_today()`, an app-layer pre-check runs — if a row already exists for `(ticker, trade_date)`, a D3 `data_guard.failed` event is emitted and the candidate is skipped via `continue`. The DB constraint is the second safety net.

## Why
Prior to 2026-07-12, `ON CONFLICT DO NOTHING` in the INSERT had no conflict target and no `(ticker, trade_date)` constraint to match — making it a no-op. A duplicate ticker+date would have silently inserted a second row invisible to all governance paths.

## How to apply
- Any future schema migration that drops/recreates `aiem_paper_trades` must re-apply `ALTER TABLE aiem_paper_trades ADD CONSTRAINT aiem_paper_trades_ticker_date_unique UNIQUE (ticker, trade_date)`.
- The pre-check block in main.py is tagged `# ── Order dedup pre-check [A7 enforcement]` — search for that tag if you need to find or verify it.
- `order_dedup.py` is a SEPARATE module operating on `order_execution_log` (keyed on `decision_id`). It is NOT wired to the paper trading path. Do not confuse the two systems.
- `position_reconciler.py` is DOCUMENTED_DORMANT (queries `ai_stock_picks`, not `aiem_paper_trades`; no broker API exists yet). See risk-gate-enforcement-gaps.md.

## Dev/prod schema drift note
`mid_price`, `fill_price`, `spread_pct_used` exist in prod `aiem_paper_trades` but NOT in dev. The app INSERT references them; direct test inserts in dev must omit those columns. The pre-check itself is unaffected (queries only ticker+trade_date).

## Controlled test evidence (2026-07-12)
- DEDUP_TEST / 2099-01-01 row: `aiem_paper_trades.id=3`
- D3 governance event: `d3_governance_event_links.id=96`, phase=`D2_DATA_GUARD_FAILED`, ticker=DEDUP_TEST
- All 5 assertions PASS (baseline, first insert, pre-check block, constraint UniqueViolation, final count=1)
- DEDUP_TEST row NOT deleted — pending user approval
