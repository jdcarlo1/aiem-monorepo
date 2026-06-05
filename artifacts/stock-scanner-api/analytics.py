"""
Historical analytics engine.

For each ticker, computes the composite score for every trading day
(vectorized — no sliding-window recomputation) and records the actual
next-1d / 3d / 5d returns.  Results are aggregated into score buckets
so the dashboard can show win-rates and average returns by score range.
"""

import numpy as np
import pandas as pd
import yfinance as yf
import ta


# ---------------------------------------------------------------------------
# Vectorized scoring — mirrors scoring.py logic exactly
# ---------------------------------------------------------------------------

def _score_series(df: pd.DataFrame) -> pd.Series:
    close = df["Close"].squeeze().astype(float)
    volume = df["Volume"].squeeze().astype(float)

    # --- RSI ---
    rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    rsi_pts = np.where(
        (rsi >= 40) & (rsi <= 60), 2,
        np.where((rsi >= 30) & (rsi < 40), 1,
        np.where(rsi < 30, 1,
        np.where((rsi > 60) & (rsi <= 70), 1, 0)))
    )

    # --- MACD ---
    macd_obj = ta.trend.MACD(close=close)
    macd = macd_obj.macd()
    macd_sig = macd_obj.macd_signal()
    macd_hist = macd_obj.macd_diff()
    macd_pts = np.where(
        (macd > macd_sig) & (macd_hist > 0), 2,
        np.where(macd > macd_sig, 1, 0)
    )

    # --- Trend / SMA ---
    sma50 = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
    sma200 = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
    trend_pts = np.where(
        (close > sma50) & (sma50 > sma200), 2,
        np.where((close > sma50) | (close > sma200), 1, 0)
    )

    # --- Volume ---
    avg_vol = volume.rolling(20).mean()
    vol_ratio = volume / avg_vol.replace(0, np.nan)
    vol_pts = np.where(vol_ratio >= 2.0, 2, np.where(vol_ratio >= 1.3, 1, 0))

    # --- Bollinger Bands ---
    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    bb_mid   = bb.bollinger_mavg()
    near_lower = np.abs(close - bb_lower) < np.abs(close - bb_upper)
    bb_pts = np.where(
        (close > bb_mid) & (close < bb_upper), 2,
        np.where(near_lower & (close < bb_mid), 1,
        np.where(close > bb_upper, 1, 0))
    )

    raw = pd.Series(
        rsi_pts.astype(float) + macd_pts.astype(float) +
        trend_pts.astype(float) + vol_pts.astype(float) + bb_pts.astype(float),
        index=df.index,
    )

    # Normalize: max_raw = 10, so normalized = raw (same scale)
    normalized = (raw / 10.0 * 10.0).clip(1, 10)

    # Mask rows where we don't yet have enough history
    valid = ~(rsi.isna() | sma200.isna() | macd_sig.isna() | bb_mid.isna())
    normalized[~valid] = np.nan

    return normalized


# ---------------------------------------------------------------------------
# Score-bucket helpers
# ---------------------------------------------------------------------------

BUCKETS = [(1,3), (3,5), (5,6), (6,7), (7,8), (8,10)]
BUCKET_LABELS = ["1–3", "3–5", "5–6", "6–7", "7–8", "8–10"]


def _assign_bucket(score: float) -> str | None:
    for (lo, hi), label in zip(BUCKETS, BUCKET_LABELS):
        if lo <= score < hi or (hi == 10 and score <= 10):
            return label
    return None


# ---------------------------------------------------------------------------
# Per-ticker analysis
# ---------------------------------------------------------------------------

def _analyze_ticker_history(ticker: str) -> pd.DataFrame | None:
    try:
        data = yf.download(ticker, period="2y", progress=False, auto_adjust=True)
    except Exception:
        return None

    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    scores = _score_series(data)
    close = data["Close"].squeeze().astype(float)

    ret1 = (close.shift(-1) / close - 1) * 100
    ret3 = (close.shift(-3) / close - 1) * 100
    ret5 = (close.shift(-5) / close - 1) * 100

    df_out = pd.DataFrame({
        "ticker": ticker,
        "date": data.index,
        "score": scores.values,
        "close": close.values,
        "ret_1d": ret1.values,
        "ret_3d": ret3.values,
        "ret_5d": ret5.values,
    })

    # Drop rows without a score or without forward returns
    df_out = df_out.dropna(subset=["score", "ret_1d"])
    df_out["bucket"] = df_out["score"].apply(_assign_bucket)
    return df_out


# ---------------------------------------------------------------------------
# Aggregate across tickers
# ---------------------------------------------------------------------------

def run_historical_analytics(tickers: list[str]) -> dict:
    frames = []
    errors = []

    for t in tickers:
        df = _analyze_ticker_history(t)
        if df is not None and len(df) > 0:
            frames.append(df)
        else:
            errors.append(t)

    if not frames:
        return {"error": "No data could be fetched", "failed": errors}

    all_data = pd.concat(frames, ignore_index=True)

    # --- Score distribution (how many days in each bucket) ---
    dist = (
        all_data.groupby("bucket")
        .size()
        .reindex(BUCKET_LABELS, fill_value=0)
        .reset_index()
        .rename(columns={"bucket": "bucket", 0: "count"})
    )
    score_distribution = dist.to_dict(orient="records")

    # --- Stats by bucket ---
    bucket_stats = []
    for label in BUCKET_LABELS:
        sub = all_data[all_data["bucket"] == label]
        n = len(sub)
        if n == 0:
            bucket_stats.append({
                "bucket": label, "count": 0,
                "win_rate_1d": None, "win_rate_3d": None, "win_rate_5d": None,
                "avg_ret_1d": None, "avg_ret_3d": None, "avg_ret_5d": None,
                "median_ret_1d": None,
            })
            continue

        wr1 = float((sub["ret_1d"] > 0).mean() * 100)
        wr3 = float((sub["ret_3d"] > 0).mean() * 100)
        wr5 = float((sub["ret_5d"] > 0).mean() * 100)
        ar1 = float(sub["ret_1d"].mean())
        ar3 = float(sub["ret_3d"].mean())
        ar5 = float(sub["ret_5d"].mean())
        mr1 = float(sub["ret_1d"].median())

        bucket_stats.append({
            "bucket": label,
            "count": int(n),
            "win_rate_1d": round(wr1, 1),
            "win_rate_3d": round(wr3, 1),
            "win_rate_5d": round(wr5, 1),
            "avg_ret_1d": round(ar1, 3),
            "avg_ret_3d": round(ar3, 3),
            "avg_ret_5d": round(ar5, 3),
            "median_ret_1d": round(mr1, 3),
        })

    # --- Best thresholds (score >= X) ---
    thresholds = []
    for threshold in [5.0, 6.0, 6.5, 7.0, 7.5, 8.0]:
        sub = all_data[all_data["score"] >= threshold]
        n = len(sub)
        if n < 10:
            continue
        thresholds.append({
            "threshold": threshold,
            "count": int(n),
            "win_rate_1d": round(float((sub["ret_1d"] > 0).mean() * 100), 1),
            "win_rate_3d": round(float((sub["ret_3d"] > 0).mean() * 100), 1),
            "win_rate_5d": round(float((sub["ret_5d"] > 0).mean() * 100), 1),
            "avg_ret_1d": round(float(sub["ret_1d"].mean()), 3),
            "avg_ret_3d": round(float(sub["ret_3d"].mean()), 3),
            "avg_ret_5d": round(float(sub["ret_5d"].mean()), 3),
        })

    # --- Overall ML-style stats ---
    total_days = len(all_data)
    overall_win_1d = round(float((all_data["ret_1d"] > 0).mean() * 100), 1)

    return {
        "tickers_analyzed": [t for df in frames for t in [df["ticker"].iloc[0]]],
        "failed": errors,
        "total_observations": total_days,
        "overall_win_rate_1d": overall_win_1d,
        "score_distribution": score_distribution,
        "bucket_stats": bucket_stats,
        "best_thresholds": thresholds,
    }
