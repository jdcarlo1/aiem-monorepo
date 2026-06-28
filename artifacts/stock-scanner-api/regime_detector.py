"""
regime_detector.py
====================================================================
Bridge between regime_macro_patch.py and market_regime_overlay.py.

Provides:
  - get_current_regime(db_url, proxy_ticker) — fetches real price + VIX
    history and delegates to market_regime_overlay.combine_regime_votes().
  - REGIME_SIGNAL_MULTIPLIERS — position-size / confidence multipliers
    keyed by recommendation string; consumed by regime_macro_patch.py.

Falls back gracefully if data sources are unavailable — never crashes.
====================================================================
"""

import datetime as dt
from typing import Dict, Any

import pandas as pd

REGIME_SIGNAL_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "full_exposure": {
        "confidence_multiplier":      1.0,
        "position_size_multiplier":   1.0,
        "exit_sensitivity":           1.0,
    },
    "reduce_exposure": {
        "confidence_multiplier":      0.7,
        "position_size_multiplier":   0.7,
        "exit_sensitivity":           1.2,
    },
    "sit_out": {
        "confidence_multiplier":      0.3,
        "position_size_multiplier":   0.0,
        "exit_sensitivity":           1.5,
    },
}

_FALLBACK_REGIME = {
    "regime":           "unknown",
    "recommendation":   "reduce_exposure",
    "confidence":       "low",
    "multipliers":      REGIME_SIGNAL_MULTIPLIERS["reduce_exposure"],
    "checked_at":       None,
    "note":             "data unavailable — defaulting to reduce_exposure",
}


def get_current_regime(db_url: str, proxy_ticker: str = "SPY") -> Dict[str, Any]:
    """
    Fetches price history for proxy_ticker and VIX history from Yahoo Finance,
    then calls market_regime_overlay.combine_regime_votes() to classify the
    current market regime.

    Returns the combine_regime_votes() result dict, augmented with:
      - 'regime'     : the recommendation string (full_exposure / reduce_exposure / sit_out)
      - 'multipliers': the REGIME_SIGNAL_MULTIPLIERS entry for that recommendation

    Falls back to a conservative reduce_exposure baseline if any data fetch or
    computation fails — never raises.
    """
    try:
        from market_regime_overlay import combine_regime_votes
    except ImportError:
        return dict(_FALLBACK_REGIME, note="market_regime_overlay not importable")

    try:
        import yfinance as yf

        price_df = yf.download(
            proxy_ticker, period="6mo", progress=False, auto_adjust=True
        )
        vix_raw = yf.download(
            "^VIX", period="6mo", progress=False, auto_adjust=True
        )

        if isinstance(price_df.columns, pd.MultiIndex):
            price_df.columns = price_df.columns.get_level_values(0)
        if isinstance(vix_raw.columns, pd.MultiIndex):
            vix_raw.columns = vix_raw.columns.get_level_values(0)

        price_df = price_df.reset_index()
        price_df.columns = [c.lower() for c in price_df.columns]

        vix_hist = (
            vix_raw["Close"].squeeze().dropna()
            if not vix_raw.empty and "Close" in vix_raw.columns
            else pd.Series(dtype=float)
        )

        if price_df.empty or vix_hist.empty or len(price_df) < 30:
            return dict(_FALLBACK_REGIME, note="insufficient price history")

    except Exception as e:
        return dict(_FALLBACK_REGIME, note=f"data fetch failed: {e}")

    try:
        result = combine_regime_votes(
            vix_history=vix_hist,
            price_history=price_df,
        )
        rec = result.get("recommendation", "reduce_exposure")
        multipliers = REGIME_SIGNAL_MULTIPLIERS.get(
            rec, REGIME_SIGNAL_MULTIPLIERS["reduce_exposure"]
        )
        result["regime"]      = rec
        result["multipliers"] = multipliers
        result["checked_at"]  = dt.datetime.utcnow().isoformat()
        return result

    except Exception as e:
        return dict(_FALLBACK_REGIME, note=f"combine_regime_votes failed: {e}")


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        result = get_current_regime(db_url)
        print(f"regime={result.get('regime')}  recommendation={result.get('recommendation')}")
        print(f"multipliers={result.get('multipliers')}")
    else:
        print("Set DATABASE_URL to test against real data.")
