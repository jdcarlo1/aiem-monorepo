"""
walk_forward.py - expanding-window walk-forward validation per horizon,
using the existing data_prep.walk_forward_splits() (never a random shuffle
split on time-series data). Compares the trained model against the existing
rule_based_baseline_predict() rule (rvol>=2 & gap_pct>=1.0) from
model_training.py, and flags any fold where the model's high-confidence win
rate exceeds 65-70% - per the architect's plan, an unusually high in-sample
win rate at this data volume is itself a leakage red flag worth a second
look, not a result to celebrate uncritically.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_training import train_model, rule_based_baseline_predict
from evaluation_metrics import classification_metrics, brier_score, precision_at_confidence_threshold

from config import HORIZONS
from data_snapshot import build_dataset
from features import add_standardized_features, STANDARDIZED_FEATURE_COLUMNS
from date_utils import date_safe_walk_forward_splits

HIGH_WR_FLAG_THRESHOLD = 0.65


def run_walk_forward(std_df: pd.DataFrame, initial_train_days=6, val_window_days=2, step_days=2) -> dict:
    all_results = {}

    for h in HORIZONS:
        label_col = f"label_{h}d"
        if label_col not in std_df.columns:
            continue

        sub = std_df.dropna(subset=[label_col]).copy()
        sub = sub.rename(columns={label_col: "outcome"})
        n_dates = sub["trade_date"].nunique()
        if n_dates < initial_train_days + val_window_days:
            print(f"\n=== Horizon {h}d: SKIPPED ({n_dates} unique dates < "
                  f"{initial_train_days + val_window_days} needed for one fold) ===")
            continue

        usable_cols_all = [c for c in STANDARDIZED_FEATURE_COLUMNS if sub[c].notna().any()]

        print(f"\n=== Horizon {h}d walk-forward (n={len(sub)} rows, {n_dates} unique dates) ===")
        fold_metrics = []
        fold_i = 0
        for train_df, val_df in date_safe_walk_forward_splits(
            sub, date_col="trade_date",
            initial_train_days=initial_train_days,
            val_window_days=val_window_days,
            step_days=step_days,
        ):
            fold_i += 1
            train_start = train_df["trade_date"].min()
            train_end = train_df["trade_date"].max()
            val_start = val_df["trade_date"].min()
            val_end = val_df["trade_date"].max()

            overlap = train_end >= val_start
            usable_cols = [c for c in usable_cols_all if train_df[c].notna().any()]

            model = train_model(train_df, feature_columns=usable_cols)
            proba = pd.Series(
                model.model.predict_proba(val_df[usable_cols])[:, 1], index=val_df.index
            )
            y_val = val_df["outcome"]

            metrics = classification_metrics(y_val, proba)
            brier = brier_score(y_val, proba)
            prec_table = precision_at_confidence_threshold(y_val, proba)

            baseline_pred = rule_based_baseline_predict(val_df) if {"rvol", "gap_pct"}.issubset(val_df.columns) else None
            baseline_wr = y_val[baseline_pred == 1].mean() if baseline_pred is not None and (baseline_pred == 1).any() else np.nan
            baseline_n = int((baseline_pred == 1).sum()) if baseline_pred is not None else 0

            high_conf = prec_table[prec_table["threshold"] >= 0.7]
            flagged = (high_conf["actual_win_rate"] > HIGH_WR_FLAG_THRESHOLD).any() if not high_conf.empty else False

            print(f"  fold {fold_i}: train [{train_start} -> {train_end}] (n={len(train_df)})  "
                  f"val [{val_start} -> {val_end}] (n={len(val_df)})  overlap={overlap}")
            print(f"    model  auc={metrics['auc']:.3f}  brier={brier:.3f}")
            print(f"    baseline (rvol>=2 & gap>=1%): n_fired={baseline_n}  win_rate={baseline_wr}")
            if flagged:
                print(f"    [FLAG] high-confidence (>=0.7) actual win rate exceeds "
                      f"{HIGH_WR_FLAG_THRESHOLD:.0%} at n={len(val_df)} - verify this isn't leakage "
                      f"before trusting it, sample size is still small.")

            fold_metrics.append({
                "fold": fold_i, "train_start": str(train_start), "train_end": str(train_end),
                "val_start": str(val_start), "val_end": str(val_end), "overlap": overlap,
                "auc": metrics["auc"], "brier": brier, "n_val": len(val_df),
                "baseline_wr": baseline_wr, "baseline_n": baseline_n, "flagged_high_wr": flagged,
            })

        if fold_metrics:
            fdf = pd.DataFrame(fold_metrics)
            print(f"  --- {h}d aggregate across {len(fdf)} folds ---")
            print(f"  mean auc   = {fdf['auc'].mean():.3f}")
            print(f"  mean brier = {fdf['brier'].mean():.3f}")
            print(f"  any overlap between train/val dates: {fdf['overlap'].any()} (must be False)")
            print(f"  folds flagged for high win rate: {fdf['flagged_high_wr'].sum()}/{len(fdf)}")

        all_results[h] = fold_metrics

    return all_results


if __name__ == "__main__":
    raw = build_dataset()
    if raw.empty:
        raise SystemExit("no dataset available")

    std_df = add_standardized_features(raw)
    run_walk_forward(std_df)
