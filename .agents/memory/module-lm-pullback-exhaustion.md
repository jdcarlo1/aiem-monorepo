---
name: Module L Pullback Re-Entry + Module M Momentum Exhaustion
description: Architecture, wiring rules, and backtest gotchas for the L/M companion modules
---

## Key decisions

**Backtest data window must start at 2024-10-01** (not 2026-01-01). `_uptrend_intact()` requires `n >= 200` bars. Fetching only 2026 YTD gives ~128 bars → all tickers fail the uptrend gate → 0 backtest rows. Fix: fetch from 2024-10-01, loop from `i=210`, gate inserts at `sig_date >= 2026-01-01`.

**Why:** `_uptrend_intact(closes, lookback=90)` enforces `n >= max(lookback, 200) = 200` to avoid spurious trend detection on short price histories.

**SPY alignment: use dict lookup not `.index()`**. Per-date `.index()` on a sorted list is O(n) called n times = O(n²). Replaced with `{date: price}` dict → O(1) per date.

**register_signal() must query ALL states, not just CONFIRMED.** Module M produces only WATCHING rows in backtests (CONFIRMED requires ≥5 signals = rare). Querying `WHERE state='CONFIRMED'` gives n=0, stores NULL signal_n. Fixed to query all rows with `fwd_5d_pct IS NOT NULL`.

## Files
- `artifacts/stock-scanner-api/aiem_pullback_reentry.py` — Module L
- `artifacts/stock-scanner-api/aiem_momentum_exhaustion.py` — Module M

## DB tables
- `aiem_pullback_signals` — live scan output for L
- `aiem_pullback_backtest_log` — (ticker, signal_date) UNIQUE
- `aiem_lm_routing_log` — L→M routing events
- `aiem_lm_conflict_log` — same-ticker-same-day conflicts
- `aiem_exhaustion_signals` — live scan output for M
- `aiem_exhaustion_backtest_log` — (ticker, signal_date) UNIQUE

## API endpoints
GET: `/stock-api/pullback-reentry`, `/pullback-reentry/routing-log`, `/lm-conflict-log`, `/momentum-exhaustion`
POST (admin HMAC): `/admin/run-pullback-reentry`, `/admin/run-pullback-backtest`, `/admin/run-momentum-exhaustion`, `/admin/run-exhaustion-backtest`

## Scheduler slots
- L scan: Mon-Fri 10:30 + 14:30 ET (`aiem_pullback_scan_1030`, `aiem_pullback_scan_1430`)
- L backtest: Sun 23:45 ET (`aiem_pullback_backtest_weekly`)
- M scan: Mon-Fri 10:45 + 14:45 ET — AFTER L so conflict table is populated first
- M backtest: Mon 00:05 ET (`aiem_exhaust_backtest_weekly`)

## S8 spec compliance
`cross_market_speculative_rollover_count` is TEXT column, DEFAULT='NOT_IMPLEMENTED'. Code explicitly guards against integer 0. Live API response includes `"s8_status": "NOT_IMPLEMENTED"`.

## Backtest results (2026 YTD as of 2026-07-05)
- Module L: 33,578 rows; WATCHING WR=50.8%/avg+0.48%; CONFIRMED WR=44.6%/avg-0.13%; FP=74-88.5%
- Module M: 3,776 rows; catch_rate@3-sig=43.6%, @4-sig=85.7%; FP@3=18.7%, FP@4=0%
- No OOS validation done; RSI thresholds are spec-derived (50/45), not optimized to this data
