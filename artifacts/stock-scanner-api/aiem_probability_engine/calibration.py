"""
calibration.py - fits a Platt (sigmoid) or isotonic calibrator per horizon
on a held-out validation fold, then verifies on a SEPARATE held-out test
fold using evaluation_metrics.calibration_curve_table() so the calibration
check itself isn't leaking into the same data it was fit on.

Three-way time split per horizon (via data_prep.simple_time_split):
  train (60%)      -> fits the base classifier
  validation (20%) -> fits the calibrator against the base classifier's
                       raw (uncalibrated) predictions
  test (20%)       -> verification only, touched by nothing else

Method choice: Platt/sigmoid by default (robust on the small validation
folds this dataset currently has, ~60-70 rows); isotonic only if the
validation fold clears 300 rows, since isotonic's step function overfits
on small samples. This is a data-size decision, not a preference - revisit
once validation folds grow.
"""
import os
import sys
import pickle

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_training import train_model, MIN_SAMPLES
from evaluation_metrics import calibration_curve_table, brier_score

from config import HORIZONS, MODEL_DIR
from data_snapshot import build_dataset
from features import add_standardized_features, STANDARDIZED_FEATURE_COLUMNS
from date_utils import date_safe_three_way_split, assert_no_date_overlap

ISOTONIC_MIN_VAL_SAMPLES = 300


def _fit_calibrator(raw_proba: np.ndarray, y_true: np.ndarray, method: str):
    if method == "isotonic":
        cal = IsotonicRegression(out_of_bounds="clip")
        cal.fit(raw_proba, y_true)
        return cal
    # Platt/sigmoid: fit a 1-D logistic regression on the raw score.
    cal = LogisticRegression()
    cal.fit(raw_proba.reshape(-1, 1), y_true)
    return cal


def _apply_calibrator(cal, method: str, raw_proba: np.ndarray) -> np.ndarray:
    if method == "isotonic":
        return cal.predict(raw_proba)
    return cal.predict_proba(raw_proba.reshape(-1, 1))[:, 1]


def calibrate_all_horizons(std_df: pd.DataFrame) -> dict:
    results = {}

    for h in HORIZONS:
        label_col = f"label_{h}d"
        if label_col not in std_df.columns:
            continue

        sub = std_df.dropna(subset=[label_col]).copy()
        sub = sub.rename(columns={label_col: "outcome"})
        if len(sub) < 30:
            print(f"\n=== Horizon {h}d: SKIPPED (n={len(sub)} < 30, "
                  f"not enough for a 3-way split) ===")
            continue

        usable_cols = [c for c in STANDARDIZED_FEATURE_COLUMNS if sub[c].notna().any()]

        split = date_safe_three_way_split(sub, date_col="trade_date", train_frac=0.6, val_frac=0.2)
        assert_no_date_overlap(split.train_dates, split.val_dates, split.test_dates)
        train_df, val_df, test_df = split.train, split.validation, split.test

        print(f"\n=== Horizon {h}d ===")
        print(f"  unique dates: train={len(split.train_dates)}  val={len(split.val_dates)}  "
              f"test={len(split.test_dates)}  (date-safe split, no overlap)")
        print(f"  rows: train={len(train_df)}  val={len(val_df)}  test={len(test_df)}")

        if len(val_df) < 10 or len(test_df) < 10:
            print(f"  SKIPPED - val/test fold too small to calibrate/verify honestly")
            continue

        base = train_model(train_df, feature_columns=usable_cols)
        method = "isotonic" if len(val_df) >= ISOTONIC_MIN_VAL_SAMPLES else "platt"
        print(f"  calibration method = {method} (val fold n={len(val_df)})")

        raw_val = base.model.predict_proba(val_df[usable_cols])[:, 1]
        y_val = val_df["outcome"].values
        calibrator = _fit_calibrator(raw_val, y_val, method)

        raw_test = base.model.predict_proba(test_df[usable_cols])[:, 1]
        cal_test = _apply_calibrator(calibrator, method, raw_test)
        y_test = test_df["outcome"]

        raw_brier = brier_score(y_test, pd.Series(raw_test, index=y_test.index))
        cal_brier = brier_score(y_test, pd.Series(cal_test, index=y_test.index))

        print(f"  test brier RAW        = {raw_brier:.4f}")
        print(f"  test brier CALIBRATED = {cal_brier:.4f} "
              f"({'improved' if cal_brier < raw_brier else 'did NOT improve'})")

        print("  calibration curve (test fold, RAW):")
        print(calibration_curve_table(y_test, pd.Series(raw_test, index=y_test.index), n_bins=5)
              .to_string(index=False))
        print("  calibration curve (test fold, CALIBRATED):")
        print(calibration_curve_table(y_test, pd.Series(cal_test, index=y_test.index), n_bins=5)
              .to_string(index=False))

        artifact = {
            "base_model": base,
            "calibrator": calibrator,
            "method": method,
            "feature_columns": usable_cols,
            "raw_brier": raw_brier,
            "cal_brier": cal_brier,
            "n_train": len(train_df),
            "n_val": len(val_df),
            "n_test": len(test_df),
        }
        path = os.path.join(MODEL_DIR, f"calibrated_horizon_{h}d.pkl")
        with open(path, "wb") as f:
            pickle.dump(artifact, f)
        print(f"  saved -> {path}")

        results[h] = artifact

    return results


if __name__ == "__main__":
    raw = build_dataset()
    if raw.empty:
        raise SystemExit("no dataset available")

    std_df = add_standardized_features(raw)
    calibrate_all_horizons(std_df)
