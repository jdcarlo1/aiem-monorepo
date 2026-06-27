"""
vwap_indicators.py
----------------------
DISTINCT FROM execution_simulator.py — that module uses TWAP/VWAP as
EXECUTION algorithms (how to fill a large order without moving the price).
This module uses VWAP as an ANALYTICAL INDICATOR — one of the most-watched
real intraday/premarket signals among actual day traders: is price trading
ABOVE or BELOW the volume-weighted average price right now, and how far?

Why this matters as a standalone signal, distinct from simple moving
averages: VWAP weights each price by the VOLUME traded at that price, so
it reflects where the real money actually transacted, not just where price
visited. A stock trading above its premarket VWAP is trading above the
average price institutional/algorithmic volume has been paying — a
meaningfully different read than "price is above its 20-period SMA."

Three VWAP-based features, designed to slot directly into
premarket_gap_continuation_scanner.py and intraday_continuation_scanner.py's
existing FEATURE_NAMES lists:

  1. price_vs_premarket_vwap_pct — how far current price is from the
     premarket session's VWAP, as a %. Price holding above premarket VWAP
     through the open is a classically-watched strength signal among day
     traders specifically.

  2. price_vs_intraday_vwap_pct — same idea, but using the running VWAP
     from market open to now. Institutional desks often use "are we above
     or below VWAP" as their own execution benchmark, so price holding
     above intraday VWAP can reflect real buying pressure, not just
     momentum.

  3. vwap_reclaim_signal — specifically flags when price was BELOW VWAP
     and just crossed back ABOVE it (a "reclaim") — a distinct, well-known
     pattern day traders watch for, different from just "currently above."

REQUIRES: pandas, numpy.
"""

from typing import Dict, Any, Optional

import numpy as np
import pandas as pd


def compute_vwap(bars: pd.DataFrame) -> pd.Series:
    """Standard VWAP calculation: cumulative (price * volume) / cumulative
    volume, using typical price (high+low+close)/3 per bar — the standard
    convention, not just the close price, since VWAP is meant to represent
    where volume actually transacted across the bar's range."""
    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
    cumulative_pv = (typical_price * bars["volume"]).cumsum()
    cumulative_volume = bars["volume"].cumsum()
    return cumulative_pv / cumulative_volume.replace(0, np.nan)


def price_vs_vwap_pct(bars: pd.DataFrame, current_price: Optional[float] = None) -> Dict[str, Any]:
    """Computes how far current price is from the running VWAP, as a %.
    Pass current_price explicitly for a live/real-time read; otherwise
    uses the last bar's close."""
    vwap_series = compute_vwap(bars)
    current_vwap = vwap_series.iloc[-1]
    price = current_price if current_price is not None else bars["close"].iloc[-1]

    if pd.isna(current_vwap) or current_vwap == 0:
        return {"price_vs_vwap_pct": 0.0, "vwap": None, "error": "insufficient_volume_data"}

    pct = (price - current_vwap) / current_vwap * 100
    return {
        "price_vs_vwap_pct": round(float(pct), 3),
        "vwap": round(float(current_vwap), 4),
        "current_price": round(float(price), 4),
        "above_vwap": bool(pct > 0),
    }


def detect_vwap_reclaim(bars: pd.DataFrame, lookback_bars: int = 5) -> Dict[str, Any]:
    """Flags a VWAP RECLAIM: price was below VWAP within the lookback
    window and has just crossed back above it. This is a distinct,
    specifically-watched pattern — different from simply 'currently above
    VWAP,' because a reclaim signals a recent shift in control (sellers
    were winning, buyers just took it back), which day traders treat
    differently than a stock that's been steadily above VWAP all session."""
    vwap_series = compute_vwap(bars)
    if len(bars) < lookback_bars + 1:
        return {"reclaim_detected": False, "reason": "insufficient_bars"}

    recent_closes = bars["close"].iloc[-(lookback_bars + 1):]
    recent_vwap = vwap_series.iloc[-(lookback_bars + 1):]

    was_below = (recent_closes.iloc[:-1] < recent_vwap.iloc[:-1]).any()
    now_above = recent_closes.iloc[-1] > recent_vwap.iloc[-1]

    reclaim_detected = bool(was_below and now_above)

    return {
        "reclaim_detected": reclaim_detected,
        "current_price": round(float(recent_closes.iloc[-1]), 4),
        "current_vwap": round(float(recent_vwap.iloc[-1]), 4),
        "interpretation": (
            "Price just reclaimed VWAP after trading below it — a commonly-"
            "watched shift-in-control signal, worth weighting alongside other "
            "indicators rather than treating as a standalone signal."
            if reclaim_detected else
            "No recent VWAP reclaim pattern detected."
        ),
    }


def compute_vwap_features_for_scanner(
    premarket_bars: Optional[pd.DataFrame],
    intraday_bars_so_far: Optional[pd.DataFrame],
    current_price: float,
) -> Dict[str, float]:
    """Convenience function that produces exactly the feature dict to
    merge into premarket_gap_continuation_scanner.py's or
    intraday_continuation_scanner.py's feature vectors. Pass whichever of
    premarket_bars / intraday_bars_so_far you have available; missing ones
    default to 0.0 (neutral) rather than breaking the pipeline.
    """
    features = {
        "price_vs_premarket_vwap_pct": 0.0,
        "price_vs_intraday_vwap_pct": 0.0,
        "vwap_reclaim_signal": 0,
    }

    if premarket_bars is not None and len(premarket_bars) >= 3:
        pm_result = price_vs_vwap_pct(premarket_bars, current_price)
        features["price_vs_premarket_vwap_pct"] = pm_result.get("price_vs_vwap_pct", 0.0)

    if intraday_bars_so_far is not None and len(intraday_bars_so_far) >= 3:
        id_result = price_vs_vwap_pct(intraday_bars_so_far, current_price)
        features["price_vs_intraday_vwap_pct"] = id_result.get("price_vs_vwap_pct", 0.0)

        reclaim = detect_vwap_reclaim(intraday_bars_so_far)
        features["vwap_reclaim_signal"] = 1 if reclaim.get("reclaim_detected") else 0

    return features


if __name__ == "__main__":
    print("vwap_indicators: VWAP as an ANALYTICAL signal (distinct from execution_simulator's TWAP/VWAP execution algos).")
    print("Add compute_vwap_features_for_scanner()'s output to premarket_gap_continuation_scanner.py's")
    print("and intraday_continuation_scanner.py's feature dicts, then retrain those scanners with the new features.")
