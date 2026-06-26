"""
signal_correlation.py

Checks whether your 8 signal layers (OI Build, Gamma, Charm, Squeeze Fuel,
Dark Pool, Float OD, Sweep, Sector Heat) are actually contributing
independent information, or whether several of them are just echoing each
other. This is usable RIGHT NOW with your existing 10,347+ backfilled
signal rows — it does not require settled outcomes, because it's purely
about how the signals relate to each other, not whether they predict wins.

Why this matters: if Gamma and Charm are 90% correlated, your "8-layer
conviction score" is really more like 5-6 independent layers wearing extra
labels. That's not necessarily bad, but it changes how much weight any one
of them should get, and tells you where adding a 9th signal might actually
help (somewhere uncorrelated with the existing 8) vs. where it wouldn't
(another flavor of something you already have).
"""

import numpy as np
import pandas as pd
from itertools import combinations


def correlation_matrix(signal_df: pd.DataFrame, signal_columns: list) -> pd.DataFrame:
    """
    Standard Pearson correlation matrix across signal columns. Good first
    look — but only catches linear relationships.
    """
    return signal_df[signal_columns].corr()


def mutual_information_matrix(signal_df: pd.DataFrame, signal_columns: list, bins: int = 10) -> pd.DataFrame:
    """
    Mutual information between each pair of signals, after discretizing
    into bins. Catches non-linear redundancy that Pearson correlation
    misses (e.g. two signals that move together in a U-shape rather than
    a straight line).
    """
    from sklearn.feature_selection import mutual_info_regression

    n = len(signal_columns)
    mi_matrix = pd.DataFrame(np.zeros((n, n)), index=signal_columns, columns=signal_columns)

    for col_a, col_b in combinations(signal_columns, 2):
        valid = signal_df[[col_a, col_b]].dropna()
        if len(valid) < 30:
            continue

        binned_a = pd.cut(valid[col_a], bins=bins, labels=False, duplicates="drop")
        mi = mutual_info_regression(
            binned_a.values.reshape(-1, 1), valid[col_b].values, random_state=0
        )[0]
        mi_matrix.loc[col_a, col_b] = mi
        mi_matrix.loc[col_b, col_a] = mi

    return mi_matrix


def flag_redundant_pairs(corr_matrix: pd.DataFrame, threshold: float = 0.75) -> pd.DataFrame:
    """
    Returns pairs of signals whose correlation exceeds `threshold` — these
    are candidates for "this is mostly the same information twice."
    Doesn't tell you to remove either one automatically; that's a judgment
    call once you see which pairs show up.
    """
    rows = []
    columns = corr_matrix.columns
    for col_a, col_b in combinations(columns, 2):
        corr_val = corr_matrix.loc[col_a, col_b]
        if abs(corr_val) >= threshold:
            rows.append({"signal_a": col_a, "signal_b": col_b, "correlation": round(corr_val, 3)})

    return pd.DataFrame(rows).sort_values("correlation", ascending=False, key=abs) if rows else pd.DataFrame(
        columns=["signal_a", "signal_b", "correlation"]
    )


def effective_independent_signals(corr_matrix: pd.DataFrame, threshold: float = 0.75) -> dict:
    """
    Rough estimate of how many genuinely independent signal "clusters" you
    actually have, by greedily grouping signals that are highly correlated
    with each other. E.g. 8 raw signal columns might collapse to ~5
    effective clusters if 3 pairs are redundant.

    This is a simple heuristic, not a formal factor-analysis result — good
    enough to flag the issue, not precise enough to be the final word.
    """
    columns = list(corr_matrix.columns)
    clusters = []
    assigned = set()

    for col in columns:
        if col in assigned:
            continue
        cluster = [col]
        assigned.add(col)
        for other in columns:
            if other in assigned:
                continue
            if abs(corr_matrix.loc[col, other]) >= threshold:
                cluster.append(other)
                assigned.add(other)
        clusters.append(cluster)

    return {
        "n_raw_signals": len(columns),
        "n_effective_clusters": len(clusters),
        "clusters": clusters,
    }


def run_full_correlation_report(signal_df: pd.DataFrame, signal_columns: list, threshold: float = 0.75) -> dict:
    """
    Convenience wrapper producing the full picture in one call.
    """
    corr = correlation_matrix(signal_df, signal_columns)
    redundant = flag_redundant_pairs(corr, threshold)
    clusters = effective_independent_signals(corr, threshold)

    return {
        "correlation_matrix": corr,
        "redundant_pairs": redundant,
        "cluster_summary": clusters,
    }
