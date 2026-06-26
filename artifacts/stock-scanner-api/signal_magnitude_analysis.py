"""
signal_magnitude_analysis.py

Your current rules treat signals as binary cutoffs (rvol>=2, gap>=1%).
This module checks whether win rate actually scales continuously with
signal magnitude — e.g. is rvol=5 meaningfully better than rvol=2.1, or
does the relationship plateau (or even reverse) above some point?

Requires settled outcomes (same data constraint as the model training
and segment search modules) — this is about whether your features
matter more or less at different magnitudes, which only outcomes can
answer.
"""

import numpy as np
import pandas as pd


def binned_win_rate(
    df: pd.DataFrame,
    signal_column: str,
    outcome_col: str = "outcome",
    n_bins: int = 8,
    min_bin_samples: int = 20,
) -> pd.DataFrame:
    """
    Splits the signal column into n_bins equal-frequency bins (quantiles,
    not equal-width — this matters because rvol/gap distributions are
    usually heavily right-skewed) and reports win rate per bin.

    Look at the resulting table for: does win rate rise monotonically with
    the bin, plateau after some point, or peak in the middle and decline
    (which would mean "more extreme is not always better" — a real and
    useful finding if true).
    """
    valid = df[[signal_column, outcome_col]].dropna()
    if len(valid) < min_bin_samples * 2:
        return pd.DataFrame()

    try:
        valid = valid.copy()
        valid["bin"] = pd.qcut(valid[signal_column], q=n_bins, duplicates="drop")
    except ValueError:
        valid["bin"] = pd.qcut(valid[signal_column], q=max(2, n_bins // 2), duplicates="drop")

    grouped = valid.groupby("bin", observed=True).agg(
        n=(outcome_col, "count"),
        win_rate=(outcome_col, "mean"),
        signal_min=(signal_column, "min"),
        signal_max=(signal_column, "max"),
        signal_mean=(signal_column, "mean"),
    ).reset_index(drop=True)

    grouped = grouped[grouped["n"] >= min_bin_samples]
    return grouped


def detect_relationship_shape(binned_df: pd.DataFrame) -> str:
    """
    Rough classification of the win-rate-vs-magnitude shape, based on the
    sign pattern of consecutive differences. This is a heuristic for a
    quick read, not a substitute for looking at the actual table.
    """
    if len(binned_df) < 3:
        return "insufficient_bins_for_shape_detection"

    win_rates = binned_df["win_rate"].values
    diffs = np.diff(win_rates)

    if np.all(diffs >= -0.01):
        return "monotonically_increasing (more signal = better, consistently)"
    if np.all(diffs <= 0.01):
        return "monotonically_decreasing (more signal = worse — check signal direction/definition)"

    peak_idx = np.argmax(win_rates)
    if 0 < peak_idx < len(win_rates) - 1:
        return f"peaks_in_middle (best win rate at bin {peak_idx}, not the extremes — possible sweet spot)"

    return "non_monotonic_no_clear_pattern (treat with caution, may just be noise)"


def magnitude_report(
    df: pd.DataFrame,
    signal_columns: list,
    outcome_col: str = "outcome",
    n_bins: int = 8,
) -> dict:
    """
    Runs binned_win_rate + shape detection across multiple signal columns
    (e.g. rvol, gap_pct, oi_build) in one call.
    """
    report = {}
    for col in signal_columns:
        binned = binned_win_rate(df, col, outcome_col, n_bins)
        if binned.empty:
            report[col] = {"status": "insufficient_data", "table": None, "shape": None}
            continue

        report[col] = {
            "status": "ok",
            "table": binned,
            "shape": detect_relationship_shape(binned),
        }
    return report
