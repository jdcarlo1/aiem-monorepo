"""
historical_analog_search.py
---------------------------
Finds historical dates whose price+volume fingerprint most closely
resembles today's setup for a given ticker.

SIMILARITY METRIC
-----------------
Compares a fixed 10-feature vector over the 5 trading days BEFORE
signal_date against every trailing 5-day window in history:

  Feature (all normalized z-score across the trailing 252-day universe):
    f0  gap_pct          — open-vs-prior-close gap
    f1  rvol_5d          — 5-day avg volume / 90-day avg volume
    f2  range_pct        — (high - low) / low — realized daily range
    f3  close_position   — (close - low) / (high - low) — where we closed
    f4  price_trend_5d   — 5-day return
    f5  price_trend_20d  — 20-day return
    f6  vol_trend_ratio  — 5d avg vol / 20d avg vol
    f7  true_range_norm  — avg(true range / close) over 5d
    f8  atr_pct          — 14-day ATR / close
    f9  gap_fill_rate    — fraction of last 10 days where price revisited
                           the prior day's close (proxy for "gap and go" vs
                           "gap and fill" regime)

Similarity = 1 − (Euclidean distance / max_distance_in_sample).
Returns the top-k most similar past windows with their date and
the 5-day forward return that followed.

DATA SOURCE: polygon_market_daily table (same source as polygon_rvol_scan).
Falls back to yfinance if the table is empty or ticker is missing.

CALLERS: None in main.py yet. Designed to be called from AIEM's
research loop or from a /stock-api/historical-analog endpoint.
"""

import os
import datetime as dt
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No DATABASE_URL set.")
    return psycopg2.connect(url)


def _fetch_price_history(ticker: str, lookback_days: int = 504) -> Optional[pd.DataFrame]:
    """
    Pull OHLCV from polygon_market_daily. Falls back to yfinance if empty.
    Returns DataFrame with columns: date, open, high, low, close, volume.
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT date::date AS date, open_price AS open,
                           high_price AS high, low_price AS low,
                           close_price AS close, volume
                    FROM polygon_market_daily
                    WHERE ticker = %s
                    ORDER BY date DESC
                    LIMIT %s
                """, (ticker, lookback_days))
                rows = cur.fetchall()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
    except Exception:
        pass

    # yfinance fallback
    try:
        import yfinance as yf
        raw = yf.download(ticker, period="2y", progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.reset_index()
        raw.columns = [c.lower() for c in raw.columns]
        raw = raw.rename(columns={"date": "date"})
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in raw.columns:
                raw[col] = np.nan
        return raw[["date", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    except Exception:
        return None


def _compute_feature_vector(df: pd.DataFrame, end_idx: int) -> Optional[np.ndarray]:
    """
    Build the 10-feature vector for the 5-day window ending at end_idx.
    Returns None if insufficient data.
    """
    window = 5
    if end_idx < window + 20:
        return None

    w = df.iloc[end_idx - window: end_idx]
    hist = df.iloc[:end_idx]

    try:
        # f0: gap_pct (avg over window)
        prior_closes = hist["close"].values
        gaps = []
        for i in range(end_idx - window, end_idx):
            if i > 0:
                gap = (df["open"].iloc[i] - df["close"].iloc[i - 1]) / df["close"].iloc[i - 1]
                gaps.append(gap)
        f0 = float(np.mean(gaps)) if gaps else 0.0

        # f1: rvol_5d
        vol_5d = w["volume"].mean()
        vol_90d = hist["volume"].tail(90).mean()
        f1 = float(vol_5d / vol_90d) if vol_90d > 0 else 1.0

        # f2: range_pct (avg daily range)
        range_pct = ((w["high"] - w["low"]) / w["low"].clip(lower=0.01)).mean()
        f2 = float(range_pct)

        # f3: close_position (avg of daily close position within bar)
        denom = (w["high"] - w["low"]).clip(lower=0.0001)
        close_pos = ((w["close"] - w["low"]) / denom).mean()
        f3 = float(close_pos)

        # f4: price_trend_5d
        if len(hist) >= 6:
            f4 = float((w["close"].iloc[-1] - hist["close"].iloc[end_idx - window - 1])
                       / hist["close"].iloc[end_idx - window - 1])
        else:
            f4 = 0.0

        # f5: price_trend_20d
        if len(hist) >= 21:
            f5 = float((w["close"].iloc[-1] - hist["close"].iloc[-21])
                       / hist["close"].iloc[-21])
        else:
            f5 = 0.0

        # f6: vol_trend_ratio
        vol_20d = hist["volume"].tail(20).mean()
        f6 = float(vol_5d / vol_20d) if vol_20d > 0 else 1.0

        # f7: true_range_norm
        tr_list = []
        for i in range(end_idx - window, end_idx):
            if i > 0:
                hl = df["high"].iloc[i] - df["low"].iloc[i]
                hc = abs(df["high"].iloc[i] - df["close"].iloc[i - 1])
                lc = abs(df["low"].iloc[i] - df["close"].iloc[i - 1])
                tr = max(hl, hc, lc)
                tr_list.append(tr / max(df["close"].iloc[i], 0.01))
        f7 = float(np.mean(tr_list)) if tr_list else 0.0

        # f8: atr_pct (14-day)
        atr_window = min(14, len(hist) - 1)
        atr_vals = []
        for i in range(len(hist) - atr_window, len(hist)):
            if i > 0:
                hl = hist["high"].iloc[i] - hist["low"].iloc[i]
                hc = abs(hist["high"].iloc[i] - hist["close"].iloc[i - 1])
                lc = abs(hist["low"].iloc[i] - hist["close"].iloc[i - 1])
                atr_vals.append(max(hl, hc, lc))
        f8 = float(np.mean(atr_vals) / max(hist["close"].iloc[-1], 0.01)) if atr_vals else 0.0

        # f9: gap_fill_rate (last 10 days)
        fill_count = 0
        check = min(10, len(hist) - 1)
        for i in range(len(hist) - check, len(hist)):
            if i > 0:
                prior_close = hist["close"].iloc[i - 1]
                day_low = hist["low"].iloc[i]
                day_high = hist["high"].iloc[i]
                if day_low <= prior_close <= day_high:
                    fill_count += 1
        f9 = float(fill_count / check) if check > 0 else 0.5

        return np.array([f0, f1, f2, f3, f4, f5, f6, f7, f8, f9], dtype=float)

    except Exception:
        return None


def find_historical_analogs(
    ticker: str,
    top_k: int = 5,
    min_history_days: int = 252,
) -> List[Dict[str, Any]]:
    """
    For a given ticker, computes the 10-feature vector for the MOST RECENT
    5-day window and finds the top_k most similar past 5-day windows in the
    ticker's own history.

    Returns list of dicts, each with:
      date            — the END date of the analog window
      similarity      — 0-1 score (1 = identical fingerprint)
      features        — the feature vector of the analog
      fwd_return_5d   — actual 5-day forward return that followed (None at edges)
      summary         — human-readable summary of what the analog looked like
    """
    df = _fetch_price_history(ticker, lookback_days=max(min_history_days + 50, 600))
    if df is None or len(df) < min_history_days:
        return [{"error": f"Insufficient price history for {ticker} "
                          f"(got {len(df) if df is not None else 0}, need {min_history_days})"}]

    # Current fingerprint = last 5 days
    current_vec = _compute_feature_vector(df, len(df))
    if current_vec is None:
        return [{"error": f"Could not compute feature vector for {ticker}"}]

    # Compute feature vector for every past window
    candidates = []
    for end_idx in range(30, len(df) - 6):  # leave 6 days for forward return
        vec = _compute_feature_vector(df, end_idx)
        if vec is None:
            continue
        dist = float(np.linalg.norm(current_vec - vec))
        fwd_ret = float(
            (df["close"].iloc[end_idx + 5] - df["close"].iloc[end_idx])
            / df["close"].iloc[end_idx]
        )
        candidates.append({
            "end_idx":  end_idx,
            "date":     df["date"].iloc[end_idx].strftime("%Y-%m-%d"),
            "dist":     dist,
            "fwd_return_5d": round(fwd_ret * 100, 2),
            "vec":      vec,
        })

    if not candidates:
        return [{"error": "No valid analog windows found"}]

    # Normalize distances to 0-1 similarity
    max_dist = max(c["dist"] for c in candidates) or 1.0
    for c in candidates:
        c["similarity"] = round(1.0 - c["dist"] / max_dist, 4)

    # Sort by similarity descending, take top_k
    top = sorted(candidates, key=lambda x: x["similarity"], reverse=True)[:top_k]

    results = []
    for c in top:
        vec = c["vec"]
        summary = (
            f"gap={vec[0]*100:+.1f}%, rvol={vec[1]:.2f}x, "
            f"range={vec[2]*100:.1f}%, close_pos={vec[3]:.0%}, "
            f"5d_ret={vec[4]*100:+.1f}%, 20d_ret={vec[5]*100:+.1f}%"
        )
        results.append({
            "date":          c["date"],
            "similarity":    c["similarity"],
            "fwd_return_5d": c["fwd_return_5d"],
            "features": {
                "gap_pct":         round(float(vec[0]) * 100, 2),
                "rvol_5d":         round(float(vec[1]), 2),
                "range_pct":       round(float(vec[2]) * 100, 2),
                "close_position":  round(float(vec[3]), 2),
                "price_trend_5d":  round(float(vec[4]) * 100, 2),
                "price_trend_20d": round(float(vec[5]) * 100, 2),
                "vol_trend_ratio": round(float(vec[6]), 2),
            },
            "summary": summary,
        })

    # Add current fingerprint for context
    results.insert(0, {
        "date":         "CURRENT",
        "similarity":   1.0,
        "fwd_return_5d": None,
        "features": {
            "gap_pct":         round(float(current_vec[0]) * 100, 2),
            "rvol_5d":         round(float(current_vec[1]), 2),
            "range_pct":       round(float(current_vec[2]) * 100, 2),
            "close_position":  round(float(current_vec[3]), 2),
            "price_trend_5d":  round(float(current_vec[4]) * 100, 2),
            "price_trend_20d": round(float(current_vec[5]) * 100, 2),
            "vol_trend_ratio": round(float(current_vec[6]), 2),
        },
        "summary": (
            f"gap={current_vec[0]*100:+.1f}%, rvol={current_vec[1]:.2f}x, "
            f"range={current_vec[2]*100:.1f}%, close_pos={current_vec[3]:.0%}, "
            f"5d_ret={current_vec[4]*100:+.1f}%"
        ),
    })
    return results
