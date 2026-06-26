"""
holding_period_optimizer.py

Your backtested win rates are currently reported at fixed checkpoints
(T+1: 71%, T+3: 81%). This module tests a fuller range of holding periods
to find whether there's a better exit point than what's currently assumed
— and, importantly, whether the "best" holding period differs by signal
type or sector (which is why this pairs well with niche_segment_finder.py).

Requires: for each settled pick, a price series covering entry date
through some number of days after entry (e.g. entry + 10 trading days),
not just a single final outcome. If your data only stores final outcome
today, this needs a schema change first.
"""

import numpy as np
import pandas as pd


def returns_at_each_horizon(
    pick_id,
    entry_price: float,
    price_series: pd.Series,  # indexed by trading-days-after-entry: 0, 1, 2, ...
    max_horizon: int = 10,
) -> pd.DataFrame:
    """
    For one pick, computes the % return if exited at each horizon from
    T+1 to max_horizon.
    """
    rows = []
    for t in range(1, max_horizon + 1):
        if t not in price_series.index:
            continue
        exit_price = price_series.loc[t]
        if entry_price == 0 or np.isnan(entry_price) or np.isnan(exit_price):
            continue
        return_pct = (exit_price - entry_price) / entry_price
        rows.append({"pick_id": pick_id, "horizon": t, "return_pct": return_pct})

    return pd.DataFrame(rows)


def aggregate_horizon_performance(
    all_picks_returns: pd.DataFrame,  # columns: pick_id, horizon, return_pct
    win_threshold_pct: float = 0.0,
) -> pd.DataFrame:
    """
    Aggregates by horizon to show: win rate, average return, Sharpe-style
    consistency (mean/std), at each exit day.
    """
    def sharpe_like(returns):
        if returns.std() == 0 or len(returns) < 2:
            return np.nan
        return returns.mean() / returns.std()

    grouped = all_picks_returns.groupby("horizon").agg(
        n=("return_pct", "count"),
        win_rate=("return_pct", lambda x: (x > win_threshold_pct).mean()),
        avg_return_pct=("return_pct", "mean"),
        median_return_pct=("return_pct", "median"),
        return_consistency=("return_pct", sharpe_like),
    ).reset_index()

    return grouped.sort_values("horizon")


def find_optimal_horizon(horizon_performance: pd.DataFrame, optimize_for: str = "avg_return_pct") -> dict:
    """
    optimize_for: "win_rate", "avg_return_pct", or "return_consistency".
    """
    if horizon_performance.empty:
        return {"best_horizon": None, "reason": "no_data"}

    ranked = horizon_performance.sort_values(optimize_for, ascending=False)
    best_row = ranked.iloc[0]

    return {
        "best_horizon": int(best_row["horizon"]),
        "metric_used": optimize_for,
        "metric_value": float(best_row[optimize_for]),
        "n_samples_at_best_horizon": int(best_row["n"]),
        "full_ranked_table": ranked,
    }


def horizon_performance_by_segment(
    all_picks_returns: pd.DataFrame,
    pick_metadata: pd.DataFrame,  # must include pick_id + a segment column
    segment_column: str,
    win_threshold_pct: float = 0.0,
) -> dict:
    """
    Runs the horizon analysis separately per segment value (e.g. per
    sector), since the optimal exit timing for semiconductor gap-ups might
    genuinely differ from biotech catalyst plays.
    """
    merged = all_picks_returns.merge(pick_metadata[["pick_id", segment_column]], on="pick_id", how="left")

    results = {}
    for segment_value in merged[segment_column].dropna().unique():
        segment_data = merged[merged[segment_column] == segment_value]
        perf = aggregate_horizon_performance(segment_data, win_threshold_pct)
        results[segment_value] = perf

    return results
