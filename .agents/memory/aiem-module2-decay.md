---
name: AIEM Module 2 — Decay & Failure Analyzer
description: Covers design decisions, evaluation_status 4-value system, classification order, wiring points, and what is still pending per signal.
---

## What Module 2 does

Evaluates every row in `aiem_signal_discoveries` and assigns one of four explicit
`evaluation_status` values. No signal is silently skipped. No verdict is issued
without genuine OOS evidence.

## File and wiring

- `artifacts/stock-scanner-api/aiem_module2_decay.py` — standalone module
- `main.py` import: `import aiem_module2_decay as _m2` (try/except, line ~122)
- `POST /stock-api/admin/run-module2-decay` — triggers evaluation, returns all 9 results
- `GET /stock-api/aiem/module2-status` — returns last stored evaluations from DB
- `aiem_module2_evaluations` table — upserted on every run, UNIQUE(discovery_id)

## Classification order (critical — do not change)

1. Structural check → `unevaluable_structural` (before any other check)
2. **Outcome-exists shortcut** → if `retestable=True` outcome exists, skip condition-key
   analysis and go directly to verdict (this is why id=1 reaches `evaluable_now` even though
   its condition keys `vol_lookback/vol_ratio_min/price_range_max_pct` aren't in the parser)
3. Condition-key gap → `evaluable_pending_columns` with specific per-key reason
4. All keys mappable, no outcome → `evaluable_pending_time`
5. n >= 30 → `evaluable_now` + decay verdict

**Why:** Step 2 must come before step 3 or signals with an existing retestable=True
outcome get wrongly blocked as `evaluable_pending_columns`. Confirmed bug and fix on 2026-07-02.

## Current state of all 9 signals (as of 2026-07-02)

| id | db_status | evaluation_status | decay_verdict | n | notes |
|----|-----------|-------------------|---------------|---|-------|
| 1  | validated | evaluable_now | **failing** | 905 | wr=46.96% vs 52.35% disc, p=0.0 |
| 2  | hypothesis | evaluable_pending_columns | — | — | needs inside_day_flag, prev_close_strength, move_pct_lag1, avg_vol, price_range BETWEEN |
| 3  | hypothesis | evaluable_pending_columns | — | — | same as 2 minus price_range |
| 4  | hypothesis | evaluable_pending_time | insufficient_n | 9 | adapter ran, needs more fwd days |
| 5  | retired | evaluable_pending_columns | — | — | needs gap_abs, prev cs/move lags, avg_vol |
| 6  | validated | evaluable_pending_time | insufficient_n | 25 | wr=36% vs 55.35% disc, p=0.29 |
| 7  | retired | evaluable_pending_columns | — | — | data in polygon_indicators_daily; key alias `cmf20`→`cmf_20` not wired |
| 8  | retired | evaluable_pending_columns | — | — | same pattern; cmf_delta15→cmf_20_delta15 |
| 9  | hypothesis | unevaluable_structural | — | — | ~121/year fire rate, est 1.6yr to n=200 |

## Pending column mappings for ids 2,3,5

All computable retroactively from existing OHLCV in `polygon_market_daily`:
- `inside_day_flag`: `high < prev_high AND low > prev_low` (LAG columns)
- `prev_close_strength`: `LAG(close_strength,1) OVER (PARTITION BY ticker ORDER BY scan_date)`
- `move_pct_lag1`: `LAG(move_pct,1)` — already computed in V2 CTE but not in parser whitelist
- `avg_vol_20d`: 20-day rolling avg volume — in V2 CTE as `volume_avg20` but key `avg_vol_min` not mapped
- `price_range` (list): needs BETWEEN range condition support — parser only handles scalar _min/_max

## Indicator alias fix needed for ids 7,8

Data IS in `polygon_indicators_daily` (rsi_14, cmf_20, stoch_k, stoch_d) and V2 CTE
(cmf_20_lag15, rsi_14_lag15, cmf_20_delta15, rsi_14_delta15). Only the key alias
mapping (`cmf20`→`cmf_20`, operator-string format) is missing from the adapter.

## What Module 2 does NOT do

- Does not retire signals
- Does not promote signals
- Does not modify `aiem_signal_discoveries.status`
- All of those go through Module 4 (human approval gate)
