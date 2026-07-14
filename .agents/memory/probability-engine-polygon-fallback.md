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

## CRITICAL: _polygon_fallback_score() return structure (nested envelope!)
`_polygon_fallback_score()` does NOT return `{"polygon_fallback": True, ...}` at the top level.
It wraps the result via `sign_payload()` and returns: `{"envelope": {...}, "self_verify": {...}}`.
`polygon_fallback=True` is nested at `result["envelope"]["payload"]["polygon_fallback"]`.

**Correct check pattern:**
```python
_fb_payload = (result.get("envelope") or {}).get("payload") or {}
if _fb_payload.get("polygon_fallback") or _fb_payload.get("mode") == "ticker_polygon_fallback":
    # fallback is active
```

`"ticker_polygon_fallback"` is the unique discriminator stored in `mode` inside the payload.
**Never** check `result.get("polygon_fallback")` — it will always be None/False (wrong level).

## Option C hard-exclude (2026-07-14, current state)
`run_probability_engine_for_ticker` in `aiem_diagram2_stage_helpers.py` detects the nested
fallback and returns `{"status": "SKIP", "numeric_score_emitted": False, ...}`.
This causes `execute_stage` to record `status="PASS"` → Stage 13 "present" in D3 governance
→ MISSING_STAGES:13 clears on next paper trade cycle (tomorrow 9:42 AM ET).
Handler test: 5/5 PASS. Downstream check: 0 consumers of Stage 13 numeric score.

## Known behavior until root cause is fixed
- All D2 stage 13 entries return SKIP (not a score) when fallback active.
- 100% of `aiem_candidate_pipeline` rows trigger the fallback (all sources, confirmed by SQL).
- Open decision for Joel: re-wire probability engine to read `aiem_candidate_pipeline`.

## How to apply
- `polygon_market_daily` date column = `scan_date` (not trade_date).
- Scores signed + logged via `_log_live_query()` but NOT written to `aiem_probability_engine_predictions` (pit_status prevents it).
- Item 3 (MISSING_STAGES:13 clear) confirmed after next paper trade cycle (tomorrow 9:42 AM ET).
