"""
precursor_signals.py

Multi-day "pre-move" feature engineering for AIEM.

Design philosophy: most of your existing Layer 1-8 stack reads a single
day's snapshot (today's OI, today's short interest, today's dark pool %).
These functions instead look at the TREND/VELOCITY of those readings over
a rolling window, plus a few new pattern detectors (stealth accumulation,
squeeze duration, pocket pivots, insider clustering, peer decoupling).

All functions are written to be:
  - pure (no side effects, no DB calls) so you can unit test them directly
  - tolerant of missing optional columns (return NaN/None rather than crash)
  - vectorized with pandas/numpy where practical, since you're likely
    running these across thousands of tickers in the EOD batch

Expected base OHLCV dataframe `df`, sorted ascending by date, columns:
    date, open, high, low, close, volume

Each function below documents what extra columns/inputs it needs.
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 1. GENERIC ROLLING TREND / VELOCITY WRAPPER
# ---------------------------------------------------------------------------
def rolling_slope(series: pd.Series, window: int = 5) -> pd.Series:
    """
    Linear regression slope of `series` over a trailing `window`.
    Use this to turn ANY point-in-time metric you already compute
    (OI buildup score, short-interest %, float turnover, dark-pool %,
    sentiment score, etc.) into a trend feature.

    Returns slope per day (same units as series / day). Positive and
    rising = building momentum in that metric, not just a high reading today.
    """
    def _slope(y):
        y = y.values if isinstance(y, pd.Series) else np.asarray(y)
        if np.isnan(y).any() or len(y) < 2:
            return np.nan
        x = np.arange(len(y))
        # simple OLS slope
        x_mean, y_mean = x.mean(), y.mean()
        denom = ((x - x_mean) ** 2).sum()
        if denom == 0:
            return np.nan
        return ((x - x_mean) * (y - y_mean)).sum() / denom

    return series.rolling(window).apply(_slope, raw=False)


def trend_zscore(series: pd.Series, window: int = 10) -> pd.Series:
    """
    Z-score of the rolling slope vs its own recent history.
    Use this to flag when a metric's velocity is unusual, not just nonzero.
    e.g. trend_zscore(oi_score, window=10) > 2 means OI is building faster
    than its normal pace, a much stronger pre-move tell than "OI is up today."
    """
    slope = rolling_slope(series, window=5)
    mu = slope.rolling(window).mean()
    sigma = slope.rolling(window).std()
    return (slope - mu) / sigma.replace(0, np.nan)


# ---------------------------------------------------------------------------
# 2. STEALTH ACCUMULATION DIVERGENCE
#    (volume rising / RVOL climbing while price stays range-bound)
# ---------------------------------------------------------------------------
def stealth_accumulation_score(
    df: pd.DataFrame,
    rvol_window: int = 10,
    lookback: int = 5,
    price_range_thresh: float = 0.04,
) -> pd.DataFrame:
    """
    Flags the highest-value pattern from the gap list: volume building for
    several consecutive days while price stays compressed. Classic
    "someone's quietly buying/selling before the move" signature.

    Requires: df with columns [date, close, high, low, volume]

    Returns df with added columns:
      - rvol: volume / 10d avg volume
      - rvol_trend_5d: slope of rvol over last `lookback` days (rolling_slope)
      - price_range_5d: (max(high) - min(low)) / close over lookback window
      - stealth_score: 0-1, higher = stronger stealth-accumulation pattern
        (rising volume trend AND tight price range, simultaneously)
    """
    out = df.copy()
    avg_vol = out["volume"].rolling(rvol_window).mean()
    out["rvol"] = out["volume"] / avg_vol.replace(0, np.nan)
    out["rvol_trend_5d"] = rolling_slope(out["rvol"], window=lookback)

    roll_high = out["high"].rolling(lookback).max()
    roll_low = out["low"].rolling(lookback).min()
    out["price_range_5d"] = (roll_high - roll_low) / out["close"].replace(0, np.nan)

    # Normalize rvol_trend into 0-1 via simple clipping logic; tune thresholds
    # against your own backtest distribution once you have data.
    # min_periods=20 (not 60) so this doesn't silently return all-zero scores
    # until 60+ valid (non-warmup) rows exist -- with rvol_trend_5d needing
    # ~15 days of warmup itself, a strict 60-row requirement could need 75+
    # days of history before producing any nonzero score at all.
    rvol_pos = out["rvol_trend_5d"].clip(lower=0)
    rvol_rolling_max = rvol_pos.rolling(60, min_periods=20).max()
    rvol_component = (rvol_pos / rvol_rolling_max.replace(0, np.nan)).fillna(0)
    tightness_component = (price_range_thresh / out["price_range_5d"].replace(0, np.nan)).clip(upper=1).fillna(0)

    out["stealth_score"] = (rvol_component * tightness_component).clip(0, 1)
    return out


# ---------------------------------------------------------------------------
# 3. VOLATILITY SQUEEZE DURATION
#    (consecutive days of contracting range, not just "is it squeezed today")
# ---------------------------------------------------------------------------
def squeeze_duration(
    df: pd.DataFrame,
    atr_window: int = 14,
) -> pd.DataFrame:
    """
    Requires: df with columns [date, high, low, close]

    Computes ATR%, then counts consecutive days ATR% has been declining
    (a contracting-volatility streak). Long streaks = volatility coil that
    is statistically primed to expand. This is the duration metric your
    BB/Keltner squeeze score is currently missing.

    Returns df with added columns:
      - atr_pct: ATR(14) / close
      - squeeze_streak: consecutive trading days atr_pct has decreased
      - squeeze_percentile: atr_pct's percentile rank vs trailing 252 days
        (low percentile + long streak = textbook pre-breakout coil)
    """
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(atr_window).mean()
    out["atr_pct"] = atr / out["close"].replace(0, np.nan)

    declining = out["atr_pct"].diff() < 0
    # streak counter: resets to 0 whenever declining is False
    streak = declining.groupby((~declining).cumsum()).cumcount()
    out["squeeze_streak"] = np.where(declining, streak + 1, 0)

    out["squeeze_percentile"] = out["atr_pct"].rolling(252, min_periods=30).rank(pct=True)
    return out


# ---------------------------------------------------------------------------
# 4. COST-TO-BORROW TREND
# ---------------------------------------------------------------------------
def borrow_fee_trend(borrow_df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Requires: borrow_df with columns [date, borrow_fee_rate]
    (You'll need a borrow-fee data source for this — e.g. IBKR's stock
    loan API, Ortex, or a broker feed. Not available from Tradier/Polygon.)

    Rising borrow fee day-over-day = shorts paying up / getting squeezed,
    which shows up well before days-to-cover updates (that data is stale/
    biweekly per the FINRA short-interest gap already noted).

    Returns df with:
      - borrow_fee_trend: rolling_slope of fee rate over `window` days
      - borrow_fee_accel: day-over-day change in the trend itself
        (positive + accelerating = squeeze building, not just elevated)
    """
    out = borrow_df.copy()
    out["borrow_fee_trend"] = rolling_slope(out["borrow_fee_rate"], window=window)
    out["borrow_fee_accel"] = out["borrow_fee_trend"].diff()
    return out


# ---------------------------------------------------------------------------
# 5. BLOCK TRADE / DARK POOL PRINT CLUSTERING
# ---------------------------------------------------------------------------
def dark_pool_print_clustering(
    prints_df: pd.DataFrame,
    size_threshold_shares: int = 50_000,
    cluster_window_days: int = 7,
    baseline_window_days: int = 60,
) -> pd.DataFrame:
    """
    Requires: prints_df with columns [date, print_size_shares]
    (one row per off-exchange/dark-pool print; you likely already have
    this raw feed if dark_pool_score exists in your Layer 1-8 stack)

    Distinguishes "a few big prints today" from "block prints clustering
    over the last week" — clustering is the stronger institutional-
    accumulation tell.

    Returns a daily dataframe with:
      - large_print_count: count of prints >= size_threshold_shares that day
      - large_print_count_7d: rolling sum of large_print_count over
        `cluster_window_days`
      - cluster_zscore: how unusual that 7d count is vs the trailing
        60-day baseline for this ticker
    """
    daily = (
        prints_df.assign(is_large=prints_df["print_size_shares"] >= size_threshold_shares)
        .groupby("date")["is_large"]
        .sum()
        .rename("large_print_count")
        .reset_index()
    )
    daily["large_print_count_7d"] = daily["large_print_count"].rolling(cluster_window_days).sum()
    mu = daily["large_print_count_7d"].rolling(baseline_window_days).mean()
    sigma = daily["large_print_count_7d"].rolling(baseline_window_days).std()
    daily["cluster_zscore"] = (daily["large_print_count_7d"] - mu) / sigma.replace(0, np.nan)
    return daily


# ---------------------------------------------------------------------------
# 6. INSIDER CLUSTER BUYING
# ---------------------------------------------------------------------------
def insider_cluster_buy_score(
    filings_df: pd.DataFrame,
    cluster_window_days: int = 14,
) -> pd.DataFrame:
    """
    Requires: filings_df with columns [date, insider_name, transaction_type,
    shares, value] — Form 4 data (e.g. from SEC EDGAR full-text/XBRL feed
    or a vendor like Quiver Quant / OpenInsider).

    A single insider buy is noisy. Multiple DIFFERENT insiders buying
    within a tight window is a materially stronger signal.

    Returns a daily dataframe with:
      - distinct_buyers_14d: count of unique insiders who bought in the
        trailing `cluster_window_days`
      - cluster_flag: True when distinct_buyers_14d >= 3 (tune threshold
        per your backtest — 3+ unique buyers in 2 weeks is a reasonable
        starting bar)
    """
    buys = filings_df[filings_df["transaction_type"].str.lower().eq("buy")].copy()
    buys = buys.sort_values("date")

    records = []
    for _, row in buys.iterrows():
        window_start = row["date"] - pd.Timedelta(days=cluster_window_days)
        recent = buys[(buys["date"] >= window_start) & (buys["date"] <= row["date"])]
        records.append(
            {
                "date": row["date"],
                "distinct_buyers_14d": recent["insider_name"].nunique(),
            }
        )

    out = pd.DataFrame(records).drop_duplicates(subset="date").sort_values("date")
    out["cluster_flag"] = out["distinct_buyers_14d"] >= 3
    return out


# ---------------------------------------------------------------------------
# 7. SYMPATHY / PEER CORRELATION BREAKDOWN
# ---------------------------------------------------------------------------
def peer_decoupling_score(
    ticker_returns: pd.Series,
    peer_returns: pd.Series,
    window: int = 20,
    recent_window: int = 5,
) -> pd.DataFrame:
    """
    Requires: two return series (daily % change), same date index:
      - ticker_returns: the stock you're scanning
      - peer_returns: sector ETF or peer-basket average return

    A stock that normally tracks its sector but starts moving
    independently (correlation dropping) often precedes an
    idiosyncratic, stock-specific catalyst.

    Returns df with:
      - rolling_corr_20d: 20-day rolling correlation to peer/sector
      - rolling_corr_5d: 5-day rolling correlation (recent regime)
      - decoupling_delta: rolling_corr_5d - rolling_corr_20d
        (large negative = correlation breaking down right now,
        i.e. stock decoupling from its peer group)
    """
    df = pd.DataFrame({"ticker": ticker_returns, "peer": peer_returns}).dropna()
    df["rolling_corr_20d"] = df["ticker"].rolling(window).corr(df["peer"])
    df["rolling_corr_5d"] = df["ticker"].rolling(recent_window).corr(df["peer"])
    df["decoupling_delta"] = df["rolling_corr_5d"] - df["rolling_corr_20d"]
    return df


# ---------------------------------------------------------------------------
# 8. NEWS / SOCIAL VELOCITY (acceleration, not level)
# ---------------------------------------------------------------------------
def social_velocity_score(mentions_df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """
    Requires: mentions_df with columns [date, mention_count] (or
    sentiment-weighted mention count if your social_sentiment module
    already produces one)

    Reframes your existing social_sentiment output as a rate-of-change
    feature. A doubling in 2 days matters more for pre-move detection
    than an absolute "high sentiment" reading.

    Returns df with:
      - mention_pct_change_3d: % change in mention_count over `window` days
      - mention_accel: day-over-day change in mention_pct_change_3d
        (positive + rising = chatter accelerating, not just elevated)
    """
    out = mentions_df.copy()
    out["mention_pct_change_3d"] = out["mention_count"].pct_change(periods=window)
    out["mention_accel"] = out["mention_pct_change_3d"].diff()
    return out


# ---------------------------------------------------------------------------
# 9. POCKET PIVOTS (O'Neil-style early accumulation)
# ---------------------------------------------------------------------------
def pocket_pivot_flag(df: pd.DataFrame, lookback: int = 10) -> pd.DataFrame:
    """
    Requires: df with columns [date, close, volume]

    A pocket pivot day: an up-close day where volume exceeds the HIGHEST
    down-volume day of the trailing `lookback` days. Cheap to compute
    from data you already have, and a well-documented early-accumulation
    tell (often fires several days before a visible breakout).

    Returns df with:
      - is_up_day: bool
      - max_down_volume_10d: rolling max volume on down days only
      - pocket_pivot: True when today is an up day AND today's volume
        exceeds max_down_volume_10d
    """
    out = df.copy()
    out["is_up_day"] = out["close"].diff() > 0
    down_vol = out["volume"].where(~out["is_up_day"])
    out["max_down_volume_10d"] = down_vol.rolling(lookback, min_periods=1).max()
    out["pocket_pivot"] = out["is_up_day"] & (out["volume"] > out["max_down_volume_10d"])
    return out


# ---------------------------------------------------------------------------
# CONVENIENCE: bolt rolling-trend versions onto your EXISTING Layer 1-8
# scores without rewriting them. Pass in the score columns you already
# compute (oi_score, short_interest_pct, dark_pool_pct, float_turnover, etc.)
# ---------------------------------------------------------------------------
def add_trend_features_to_existing_scores(
    df: pd.DataFrame,
    score_columns: list,
    window: int = 5,
    zscore_window: int = 10,
) -> pd.DataFrame:
    """
    df: your existing daily output containing one column per Layer 1-8
        score (e.g. ['oi_buildup_score', 'short_interest_pct',
        'dark_pool_pct', 'float_turnover', 'sweep_score'])
    score_columns: list of column names in df to add trend features for

    For each column `X` in score_columns, adds:
      - f"{X}_slope_5d"
      - f"{X}_trend_zscore"  (is the velocity itself unusual)

    This is the single highest-leverage change: it turns your entire
    existing Conviction Stack into trend-aware features with ~5 lines
    per metric and no redesign of the underlying scores.
    """
    out = df.copy()
    for col in score_columns:
        if col not in out.columns:
            continue
        out[f"{col}_slope_5d"] = rolling_slope(out[col], window=window)
        out[f"{col}_trend_zscore"] = trend_zscore(out[col], window=zscore_window)
    return out
