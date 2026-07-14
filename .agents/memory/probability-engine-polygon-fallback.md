---
name: Probability Engine polygon fallback
description: ai_short_calls_log is empty; live_query.py now falls back to polygon_market_daily for stage 13 scoring
---

## Rule
`ai_short_calls_log` has 0 rows total (2026-07-14 confirmed). The probability engine model was trained on it historically, but the table is now empty. `live_query.run_live_query(mode='ticker')` must use the polygon fallback path; it will not find ai_short_calls_log rows for any D2 candidate.

## Why
D2 pipeline candidates come from gap_volume/unusual_calls/aiem_v3_discovery — none of which write to ai_short_calls_log. The original live_query.py raised RuntimeError when no ai_short_calls_log row found, causing MISSING_STAGES:13 in D3 governance.

## Fix (2026-07-14)
- `data_snapshot.py`: Added `build_single_row_for_ticker(ticker)` — reads `polygon_market_daily` (column is `scan_date`, not `trade_date`), computes pit_features, sets all options features (vol_oi, otm_pct, days_out, conviction_score, gamma_score, dark_pool_score, squeeze_score, sector_heat_score) to NaN for pipeline imputation.
- `live_query.py`: Added `_polygon_fallback_score()` + two trigger points:
  1. Early exit: `raw_df.empty + mode=='ticker'` → polygon fallback
  2. Ticker mode: `_select_ticker_row()` returns None → polygon fallback
- Polygon fallback returns `pit_status='live_unsettled_polygon_only'`, `polygon_fallback=True`, honest warning note.

## How to apply
- All D2 stage 13 scores will use polygon fallback until ai_short_calls_log is re-populated.
- Probability scores will be identical across tickers (all options features imputed to training median). Differentiation returns only when ai_short_calls_log has data.
- Scores are signed + logged via `_log_live_query()` but NOT written to `aiem_probability_engine_predictions` (pit_status prevents it).
- `polygon_market_daily` date column = `scan_date` (not trade_date).
