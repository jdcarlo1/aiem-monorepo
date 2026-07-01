"""
features.py - standardizes the raw Tier 1 + Tier 2 layer values from
data_snapshot.py into z-scores and percentiles, so heterogeneous-scale
layers (a 0-3 conviction score, a 0-100 dark_pool_score, a raw vol_oi
ratio, etc.) become comparable inputs to a single combined model.

STANDARDIZATION METHOD: trailing-window cross-sectional, not per-ticker
time-series. Reasoning (data-reality driven, not aesthetic):
  - Most tickers appear in ai_short_calls_log only once or twice across
    the ~3.5 weeks of history, so per-ticker time-series z-scoring would
    have almost no history to standardize against.
  - Each trade_date's picks already function as "that day's universe" for
    AIEM (this mirrors the existing 5-factor cross-sectional z-score
    approach used by the Nano Quant system elsewhere in this codebase).
  - For each row, its layer value is standardized against every OTHER
    picked row within the trailing STANDARDIZATION_WINDOW_DAYS calendar
    days up to and including its own trade_date. This is strictly
    point-in-time safe (never uses rows after trade_date) and works even
    on days with very few picks by pooling across the trailing window
    instead of requiring same-day-only breadth.

Revisit this once conviction_stack_watchlist (true daily full-universe
9-layer scans) has enough history to standardize against the REAL
universe instead of the picked-subset proxy used here.
"""
import numpy as np
import pandas as pd

from config import STANDARDIZATION_WINDOW_DAYS, ALL_FEATURE_COLUMNS

# day_of_week is categorical, not a magnitude to standardize.
_NO_STANDARDIZE = {"day_of_week"}
LAYER_COLUMNS = [c for c in ALL_FEATURE_COLUMNS if c not in _NO_STANDARDIZE]


def _standardize_row(value, pool: pd.Series) -> tuple:
    """Returns (zscore, percentile) of value against pool (trailing window,
    excluding rows after this row's own date by construction of the caller).
    NaN in, NaN out — never fabricate a score for missing data."""
    if pd.isna(value):
        return np.nan, np.nan
    pool = pool.dropna()
    if len(pool) < 3:
        return np.nan, np.nan
    mean, std = pool.mean(), pool.std()
    z = (value - mean) / std if std and std > 0 else np.nan
    pct = float((pool < value).mean())
    return z, pct


def add_standardized_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    df must have columns: trade_date + LAYER_COLUMNS (from data_snapshot).
    Adds {col}_z and {col}_pct for every layer column. Point-in-time safe:
    each row's pool is built only from rows with
    trade_date - STANDARDIZATION_WINDOW_DAYS <= other.trade_date <= trade_date.
    """
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out = out.sort_values("trade_date").reset_index(drop=True)

    for col in LAYER_COLUMNS:
        if col not in out.columns:
            continue
        z_vals = np.full(len(out), np.nan)
        pct_vals = np.full(len(out), np.nan)

        for i in range(len(out)):
            this_date = out.loc[i, "trade_date"]
            window_start = this_date - pd.Timedelta(days=STANDARDIZATION_WINDOW_DAYS)
            # pool = every OTHER row within the trailing window, up to and
            # including this row's own date -> no future leakage.
            mask = (out["trade_date"] >= window_start) & (out["trade_date"] <= this_date)
            pool = out.loc[mask, col]
            pool = pool.drop(index=i, errors="ignore")
            z, pct = _standardize_row(out.loc[i, col], pool)
            z_vals[i] = z
            pct_vals[i] = pct

        out[f"{col}_z"] = z_vals
        out[f"{col}_pct"] = pct_vals

    return out


STANDARDIZED_FEATURE_COLUMNS = [f"{c}_z" for c in LAYER_COLUMNS] + ["day_of_week"]


if __name__ == "__main__":
    from data_snapshot import build_dataset

    df = build_dataset()
    if df.empty:
        raise SystemExit("no dataset to standardize")

    std_df = add_standardized_features(df)

    print(f"\n--- Standardization coverage (of {len(std_df)} rows) ---")
    for col in LAYER_COLUMNS:
        z_col = f"{col}_z"
        if z_col in std_df.columns:
            print(f"  {z_col:26s} {std_df[z_col].notna().sum():4d} non-null")

    print("\n--- Sample: raw value -> z-score -> percentile (3 real rows) ---")
    sample_cols = ["ticker", "trade_date"]
    for col in ["vol_oi", "otm_pct", "dark_pool_score", "squeeze_score"]:
        sample_cols += [col, f"{col}_z", f"{col}_pct"]
    print(std_df[sample_cols].head(3).to_string(index=False))
