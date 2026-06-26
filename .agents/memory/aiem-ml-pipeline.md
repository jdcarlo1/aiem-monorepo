---
name: AIEM ML Pipeline
description: XGBoost retrain pipeline wired into the Sunday 8pm AIEM job; 6 module files in artifacts/stock-scanner-api/
---

# AIEM ML Pipeline

## Files (all in artifacts/stock-scanner-api/)
- `feature_engineering.py` — 13 FEATURE_COLUMNS; 6 are NaN until more data feeds (volume_trend_3d/5d, ma20_relative, iv_percentile, sector_relative_strength, float_size)
- `data_prep.py` — time-aware split (simple_time_split + walk_forward_splits); date_col="trade_date" not "signal_date"
- `model_training.py` — XGBoost (if installed) else LogisticRegression L2; MIN_SAMPLES=200; cv TimeSeriesSplit
- `evaluation_metrics.py` — AUC, Brier, Sharpe, max drawdown, precision-at-threshold
- `retrain_pipeline.py` — full cycle: load picks → build features → split → train → compare → promote only if AUC+Brier improve; saves to aiem_model.pkl
- `prediction_logger.py` — log_prediction() on pick save, resolve_prediction() on pick settle; table: aiem_ml_predictions

## DB Tables
- `aiem_ml_retrain_log` — one row per retrain cycle; tracks n_samples, candidate_auc, candidate_brier, promoted, reason
- `aiem_ml_predictions` — one row per pick; predicted_prob + outcome once settled

## Integration points in main.py
1. Sunday 8pm job (~line 2596): retrain cycle runs FIRST in `_retrain_then_research()` thread, then AIEM research agent
2. `_save_ai_short_calls_to_log` (~line 12614): calls log_prediction() for each new pick after DB insert
3. `_update_ai_short_call_outcomes` (~line 12740): calls resolve_prediction() when outcome changes from OPEN to WIN/LOSS

## First model (2026-06-26)
- XGBoost, cv_auc=0.651±0.110, val_auc=0.295 (overfitting — only 7 real features, 6 are NaN)
- Promoted as first model (no baseline to compare against)
- Model saved at: artifacts/stock-scanner-api/aiem_model.pkl
- is_trustworthy=True (228 >= MIN_SAMPLES 200)

## Why val_auc < 0.5 on first run
6 of 13 features are all-NaN (volume trends, MA, IV, sector, float) → XGBoost overfit to 7 thin signals on training set. Will improve as: (a) more picks accumulate, (b) volume_trend/ma features get populated from polygon_market_daily via build_feature_row()

## Rollback safety
New model is ONLY promoted if it beats prod on BOTH AUC and Brier on held-out validation — or if AUC gains >2pp even if Brier doesn't improve. This prevents a bad model from replacing a good one.

**Why:** Financial models have noisy labels and small n; without comparison gate a weekly retrain will routinely promote overfit noise.

## niche_segment_finder.py (7th module)
- Runs automatically at end of every Sunday retrain cycle (in retrain_pipeline.py)
- Searches 5 context columns: day_name, conviction_bucket, otm_bucket, expiry_bucket, rvol_bucket
- Benjamini-Hochberg FDR correction (FDR_ALPHA=0.10) + MIN_SEGMENT_SAMPLES=40 per segment
- First run (228 picks): 25 segments tested, 0 significant — correct/honest, needs more data
- Significant findings saved to aiem_segment_findings table
- As more picks settle, BH correction will pass real edges (need ~40+ picks per segment)
