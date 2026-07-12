---
name: Orchestrator layer9 column-case bug
description: compute_layer9_score() expects Title Case (Close/High/Low/Volume) but _h_layer9_statistical_edge lowercased polygon_market_daily column names first; batch scanner bug was separate
---

## Rule
Before calling `compute_layer9_score(ticker, df)` from any orchestrator handler that sources
data from `polygon_market_daily`, rename the DataFrame columns:
  `df.rename(columns={"close_price":"Close","open_price":"Open","high_price":"High","low_price":"Low","volume":"Volume"})`

## Why
`polygon_market_daily` uses snake_case (`close_price`, `high_price`, etc.).
`compute_layer9_score()` does `history_df["Close"]`, `history_df["Volume"]`, etc. (Title Case).
The orchestrator handler lowercased the columns first (`df.columns = [c.lower() for c in df.columns]`),
so the Title Case lookup raised `KeyError`, which the outer `except` swallowed, and the function
returned `_SAFE_DEFAULT` with `statistical_score=50.0` and `error="indicators_unavailable"` or
the exception string — every call, silently.

The *batch scanner* bug (positional arg passing `chain_df` to `lookback`) was a separate,
already-fixed issue (fixed in a prior session, confirmed by DB evidence: 40 rows, real values, 0 errors).

## How to apply
Any new handler that feeds `polygon_market_daily` data to `compute_layer9_score()` must do the rename.
The `_h_vwap_indicators` handler has the same pattern (it renames `close_price→close` for its own use)
and serves as a correct reference.

Also: when adding rvol from polygon_rvol_scan to `packet.technical`, use key `polygon_rvol`
(not `rvol`) to avoid being overwritten by `packet.technical.update(score)` in `_h_v3_technical`.
