---
name: Probability Engine polygon fallback
description: ai_short_calls_log is empty; probability engine has architectural mismatch — reads from AI Short Calls scanner table, not AIEM's own candidates
---

## Rule
`ai_short_calls_log` has 0 rows total (2026-07-14 confirmed). The probability engine model was trained on it historically, but the table is now empty and **this is an upstream architectural gap, not a scanner performance issue**.

## Architectural Mismatch (DO NOT misclassify as "not a bug")
The probability engine (`aiem_probability_engine/data_snapshot.py:40`) reads training and scoring data from `ai_short_calls_log`, which is written by **main.py's AI Short Calls LLM scanner** (`_run_ai_short_calls_auto()`, CronTrigger mon-fri 10:15 AM ET). That scanner is a completely separate system from AIEM. AIEM is intended to source candidates from its own pipeline (`aiem_candidate_pipeline`: gap_volume / unusual_calls / aiem_v3_discovery / aiem_ai). No module in `aiem_probability_engine/` reads `aiem_candidate_pipeline`. Needs a separate directive to re-wire.

**Why:** D2 pipeline candidates come from gap_volume/unusual_calls/aiem_v3_discovery — none of which write to `ai_short_calls_log`. The original `live_query.py` raised RuntimeError when no `ai_short_calls_log` row found, causing MISSING_STAGES:13 in D3 governance.

## Fix applied (2026-07-14) — addresses symptom, not root cause
- `data_snapshot.py`: Added `build_single_row_for_ticker(ticker)` — reads `polygon_market_daily` (column is `scan_date`, not `trade_date`), computes pit_features, sets all options features to NaN for pipeline imputation.
- `live_query.py`: Added `_polygon_fallback_score()` + two trigger points (early-exit when raw_df.empty + ticker-mode when _select_ticker_row returns None).
- Polygon fallback returns `pit_status='live_unsettled_polygon_only'`, `polygon_fallback=True`.

## Known behavior until root cause is fixed
- All D2 stage 13 scores use polygon fallback → identical 0.4473 across all tickers (non-discriminating).
- 100% of `aiem_candidate_pipeline` rows trigger the fallback (confirmed by SQL, last 30 days, all sources).
- Open decision for Joel: allow / flag / hard-exclude fallback scores from paper trade decisions (Options A/B/C).

## How to apply
- `polygon_market_daily` date column = `scan_date` (not trade_date).
- Scores signed + logged via `_log_live_query()` but NOT written to `aiem_probability_engine_predictions` (pit_status prevents it).
- Item 3 (MISSING_STAGES:13 clear) cannot be confirmed until after next paper trade cycle (tomorrow 9:42 AM ET). Before-state locked in evidence chain at ITEM3:trace_audit_before.
