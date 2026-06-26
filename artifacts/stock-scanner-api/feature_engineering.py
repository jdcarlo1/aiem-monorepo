"""
feature_engineering.py

Computes the expanded feature set for the AIEM model, beyond the original
rvol + gap rule. Each function takes a per-symbol price/volume DataFrame
(expected columns: date, open, high, low, close, volume) and returns
engineered features as of a given signal date.

Missing inputs (e.g. no IV data feed yet) should return NaN rather than
raising, so the model training step can handle partial feature sets.
"""

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "rvol",
    "gap_pct",
    "vol_oi",
    "otm_pct",
    "days_out",
    "day_of_week",
    "conviction_score",
    "volume_trend_3d",
    "volume_trend_5d",
    "ma20_relative",
    "iv_percentile",
    "sector_relative_strength",
    "float_size",
]


def compute_volume_trend(df: pd.DataFrame, signal_date, window_days: int) -> float:
    """
    Average volume over `window_days` prior to signal_date, relative to the
    prior 90-day average volume. >1.0 means recent volume is elevated.
    """
    hist = df[df["date"] < signal_date].sort_values("date")
    if len(hist) < window_days + 5:
        return np.nan

    recent = hist.tail(window_days)["volume"].mean()
    baseline = hist.tail(90)["volume"].mean()
    if baseline == 0 or np.isnan(baseline):
        return np.nan
    return recent / baseline


def compute_ma_relative(df: pd.DataFrame, signal_date, ma_window: int) -> float:
    """
    Close price on signal_date relative to its N-day moving average,
    expressed as a percentage above/below (e.g. 0.05 = 5% above MA).
    """
    hist = df[df["date"] <= signal_date].sort_values("date")
    if len(hist) < ma_window:
        return np.nan

    ma = hist["close"].tail(ma_window).mean()
    last_close = hist["close"].iloc[-1]
    if ma == 0 or np.isnan(ma):
        return np.nan
    return (last_close - ma) / ma


def compute_iv_percentile(iv_history: pd.Series, current_iv: float) -> float:
    """
    Percentile rank of current implied volatility against its own trailing
    history (e.g. 252-day). Returns NaN if no IV feed is wired up yet.
    """
    if iv_history is None or current_iv is None or len(iv_history) == 0:
        return np.nan
    return float((iv_history < current_iv).mean())


def compute_sector_relative_strength(
    symbol_returns: pd.Series, sector_returns: pd.Series
) -> float:
    """
    Trailing 10-day cumulative return of the symbol minus the same window
    for its sector/peer group ETF. Positive = outperforming sector.
    """
    if symbol_returns is None or sector_returns is None:
        return np.nan
    if len(symbol_returns) < 10 or len(sector_returns) < 10:
        return np.nan

    symbol_cum = (1 + symbol_returns.tail(10)).prod() - 1
    sector_cum = (1 + sector_returns.tail(10)).prod() - 1
    return float(symbol_cum - sector_cum)


def encode_conviction(conviction_str: str) -> float:
    """Convert text conviction level to numeric score."""
    mapping = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}
    if conviction_str is None:
        return np.nan
    return mapping.get(str(conviction_str).upper(), np.nan)


def build_feature_row(
    pick: dict,
    market_df: pd.DataFrame,
) -> dict:
    """
    Build a single feature dict for one pick. `pick` is a row from
    ai_short_calls_log. `market_df` is the polygon_market_daily rows for
    this ticker, with columns: date, close, volume, rvol, gap_pct.

    Returns NaN for any feature that cannot be computed yet.
    """
    signal_date = pick.get("trade_date")

    row = {
        "rvol":                    pick.get("rvol", np.nan),
        "gap_pct":                 pick.get("gap_pct", np.nan),
        "vol_oi":                  float(pick.get("vol_oi") or np.nan),
        "otm_pct":                 float(pick.get("otm_pct") or np.nan),
        "days_out":                float(pick.get("days_out") or np.nan),
        "day_of_week":             float(pd.Timestamp(signal_date).dayofweek) if signal_date else np.nan,
        "conviction_score":        encode_conviction(pick.get("conviction")),
        "volume_trend_3d":         np.nan,
        "volume_trend_5d":         np.nan,
        "ma20_relative":           np.nan,
        "iv_percentile":           np.nan,
        "sector_relative_strength": np.nan,
        "float_size":              np.nan,
    }

    if market_df is not None and not market_df.empty and signal_date is not None:
        df = market_df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        signal_date_d = pd.Timestamp(signal_date).date()

        row["volume_trend_3d"] = compute_volume_trend(
            df.rename(columns={"date": "date", "volume": "volume"}),
            signal_date_d, 3
        )
        row["volume_trend_5d"] = compute_volume_trend(
            df.rename(columns={"date": "date", "volume": "volume"}),
            signal_date_d, 5
        )
        row["ma20_relative"] = compute_ma_relative(
            df.rename(columns={"close_price": "close", "date": "date"}),
            signal_date_d, 20
        )

    return row
