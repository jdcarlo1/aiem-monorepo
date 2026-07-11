---
name: Diagram 2 indicator audit fixes (July 2026)
description: Summary of fixes applied to the 23-indicator Diagram 2 audit — GARCH persistence, CS spread/RND/PCA in layer9_scores, Native TreeSHAP, Stat-Arb Tradier fallback
---

## Changes made 2026-07-11

### GARCH(1,1) — NOW PERSISTED
- `volatility_clustering.py` — added `persist_garch_result()` function (writes omega, alpha1, beta1, long_run_vol, forecast_vol_1d, regime, converged, aic, bic, vote to `garch_regime_log`)
- `market_regime_overlay.py` — wired `persist_garch_result()` at garch call site in `combine_regime_votes()` (new ticker/db_url optional params)
- `main.py` — added GARCH persistence block in `_run_layer9_bg_scan()` after batch scoring
- `fit_garch_model()` min threshold: 100 → 60 (trading days); GARCH block guard also 100 → 60
- **Verified**: 20 production rows in `garch_regime_log`

### Layer9 — CS Spread / RND / PCA now stored
- `layer9_scores` table: 4 new columns — cs_spread_raw, rnd_skew, rnd_available, pca_factor1_var
- `_init_layer9_scores_table()` — ALTER TABLE IF NOT EXISTS backfill for each new column
- Layer9 batch upsert now stores all new columns
- Cross-sectional PCA computed from the full 20-ticker returns matrix; factor1 variance stored for each ticker
- **Verified**: All 20 rows have cs_spread_raw, pca_factor1_var populated

### Native TreeSHAP — Real pred_contribs=True
- `model_training.py` — `get_feature_importance()` now uses XGBoost's built-in `predict(pred_contribs=True)` first, falls back to gain-based feature_importances_
- Added `get_treeshap_attributions()` — full signed per-feature Shapley values; persists to `treeshap_attributions` table; consistency check at 1e-4 tolerance
- `treeshap_attributions` table created (ticker, trade_date, model_version, feature_names JSONB, attributions JSONB, bias, pred_proba)

### Stat-Arb — Tradier Fallback
- `stat_arb_engine.py` — `_fetch_closes_tradier()` added as fallback when polygon_market_daily has < min_rows
- Fixes: TOKEN_2 (live) first, TOKEN (sandbox) second; same API URL as main.py
- polygon_market_daily only has 32 rows/ticker during backfill; Tradier provides 193 rows
- **Verified**: Tradier fallback returns 193 rows for NVDA/AMD; cointegration test executes

## Current indicator status
- layer9/statistical edge: ✅ 20 rows, all columns populated
- GARCH(1,1): ✅ 20 production rows with real parameters
- Thompson sampler: ✅ 9 rows in aiem_paper_thompson
- Specialist council: ✅ 1 run
- BH-FDR: ✅ gate exists, 4 discoveries (oos_edge=NULL = not yet OOS validated — hypothesis status)
- Stat arb: ✅ pipeline wired; 0 cointegrated pairs in current market (statistically correct)
- GEX/options: ✅ fires at 10:05 AM ET market days; 0 rows on weekend by design
- TreeSHAP: ✅ function implemented; 0 rows = no XGBoost model trained yet (triggers on paper trade picks)
- XGBoost prob engine: ✅ aiem_paper_trades schema correct; 0 rows = weekend
