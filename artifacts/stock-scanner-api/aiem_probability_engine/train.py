"""
train.py - trains FOUR SEPARATE models, one per horizon (1d/2d/3d/4d), per
the spec's requirement that each horizon gets its own model rather than one
model whose output is sliced four ways.

Reuses model_training.py's existing pipeline (auto xgboost/logistic,
median imputation, TimeSeriesSplit CV, MIN_SAMPLES honesty gate) rather
than reinventing training/CV logic — only the feature set and per-horizon
looping are new here.
"""
import os
import sys
import pickle

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_training import train_model, get_feature_importance, MIN_SAMPLES

from config import HORIZONS, MODEL_DIR, CONFIDENT_SAMPLES_TARGET, MIN_UNIQUE_DATES_FOR_CV_TRUST
from data_snapshot import build_dataset
from features import add_standardized_features, STANDARDIZED_FEATURE_COLUMNS


def train_all_horizons(std_df: pd.DataFrame) -> dict:
    """
    Returns {horizon_days: TrainedModel}. Also saves each to
    MODEL_DIR/model_horizon_{h}d.pkl and prints an honest per-horizon report.
    """
    results = {}

    for h in HORIZONS:
        label_col = f"label_{h}d"
        if label_col not in std_df.columns:
            print(f"[train] horizon={h}d: no label column, skipping")
            continue

        sub = std_df.dropna(subset=[label_col]).copy()
        sub = sub.rename(columns={label_col: "outcome"})
        n_samples = len(sub)
        n_dates = sub["trade_date"].nunique()

        print(f"\n=== Horizon {h}d ===")
        print(f"  n_samples = {n_samples} "
              f"(MIN_SAMPLES floor = {MIN_SAMPLES}, "
              f"confident target = {CONFIDENT_SAMPLES_TARGET})")
        print(f"  n_unique_trade_dates = {n_dates}")
        if n_dates < MIN_UNIQUE_DATES_FOR_CV_TRUST:
            print(f"  [CAVEAT] model_training.py's internal CV (TimeSeriesSplit) is "
                  f"ROW-COUNT based, not date-based. With only {n_dates} unique trade "
                  f"dates, CV folds can straddle a single date's picks. Read cv_auc "
                  f"below as 'row-count sufficient, date-count immature' - NOT yet a "
                  f"validated estimate. See walk_forward.py for the date-safe check.")

        if n_samples < 5:
            print(f"  SKIPPED - fewer than 5 labeled rows, nothing to fit")
            continue

        # SimpleImputer(strategy="median") silently DROPS columns that are
        # 100% NaN (sklearn's default keep_empty_features=False), which
        # desyncs feature_columns from the pipeline's actual input width and
        # breaks get_feature_importance(). Filter those out here rather than
        # touching the shared model_training.py used elsewhere.
        usable_cols = [c for c in STANDARDIZED_FEATURE_COLUMNS if sub[c].notna().any()]
        dropped_cols = [c for c in STANDARDIZED_FEATURE_COLUMNS if c not in usable_cols]
        if dropped_cols:
            print(f"  dropped (100% NaN for this horizon's sample): {dropped_cols}")

        trained = train_model(sub, feature_columns=usable_cols)
        # Not a TrainedModel field upstream (model_training.py is shared with
        # other, higher-date-count callers) - attached here so predict.py can
        # apply the date-count confidence cap without recomputing the dataset.
        trained.n_unique_dates = n_dates

        print(f"  model_type      = {trained.model_type}")
        print(f"  is_trustworthy  = {trained.is_trustworthy} "
              f"({'>=' if trained.is_trustworthy else '<'} {MIN_SAMPLES} samples)")
        if not np.isnan(trained.cv_auc_mean):
            print(f"  cv_auc          = {trained.cv_auc_mean:.3f} +/- {trained.cv_auc_std:.3f}")
        else:
            print(f"  cv_auc          = n/a (too few samples for cross-validation)")

        try:
            importance = get_feature_importance(trained)
            print(f"  top features:")
            print(importance.head(8).to_string(index=False))
        except Exception as e:
            print(f"  feature importance unavailable: {e}")

        model_path = os.path.join(MODEL_DIR, f"model_horizon_{h}d.pkl")
        with open(model_path, "wb") as f:
            pickle.dump(trained, f)
        print(f"  saved -> {model_path}")

        results[h] = trained

    return results


if __name__ == "__main__":
    raw = build_dataset()
    if raw.empty:
        raise SystemExit("no dataset available")

    std_df = add_standardized_features(raw)
    models = train_all_horizons(std_df)

    print(f"\n=== Summary: {len(models)}/{len(HORIZONS)} horizon models trained ===")
    for h, m in models.items():
        flag = "TRUSTWORTHY" if m.is_trustworthy else "BELOW MIN_SAMPLES (use with caution)"
        auc_str = f"{m.cv_auc_mean:.3f}" if not np.isnan(m.cv_auc_mean) else "n/a"
        print(f"  {h}d: n={m.n_samples:4d}  auc={auc_str}  [{flag}]")
