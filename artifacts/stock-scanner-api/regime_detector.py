"""
regime_detector.py
====================================================================
Bridge between regime_macro_patch.py and market_regime_overlay.py.

Provides:
  - get_current_regime(db_url, proxy_ticker) — fetches real price + VIX
    history and delegates to market_regime_overlay.combine_regime_votes().
  - REGIME_SIGNAL_MULTIPLIERS — position-size / confidence multipliers
    keyed by recommendation string; consumed by regime_macro_patch.py.

Falls back gracefully if data sources are unavailable — never raises.

CACHING: results are cached for 15 min (module-level). Multiple callers
in the same APScheduler burst all get the same cached value — only one
yfinance download per 15-min window, regardless of ticker universe size.

TIMEOUT: each yfinance download is capped at 8s via a daemon thread.
If Yahoo is throttled the function returns the stale cache (or fallback)
instead of hanging the APScheduler worker.
====================================================================
"""

import datetime as dt
import threading
import time
from typing import Dict, Any, Optional

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

_REGIME_CACHE_TTL = 900.0        # 15 min: regime doesn't change tick-by-tick
_FETCH_TIMEOUT    = 8.0          # seconds before we give up on yfinance

_cache_lock   = threading.Lock()
_cached_result: Optional[Dict[str, Any]] = None
_cached_at:    float = 0.0


def _yf_download_with_timeout(symbol: str, period: str, timeout: float):
    """Downloads yfinance data in a daemon thread. Returns None on timeout."""
    import yfinance as yf
    result: list = [None]
    exc:    list = [None]

    def _run():
        try:
            result[0] = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return None
    if exc[0]:
        raise exc[0]
    return result[0]


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
    global _cached_result, _cached_at

    with _cache_lock:
        now = time.time()
        if _cached_result and (now - _cached_at) < _REGIME_CACHE_TTL:
            return _cached_result

    try:
        from market_regime_overlay import combine_regime_votes
    except ImportError:
        return dict(_FALLBACK_REGIME, note="market_regime_overlay not importable")

    try:
        price_df = _yf_download_with_timeout(proxy_ticker, "6mo", _FETCH_TIMEOUT)
        if price_df is None:
            with _cache_lock:
                if _cached_result:
                    return dict(_cached_result, note="yfinance timeout — returning stale regime")
            return dict(_FALLBACK_REGIME, note="yfinance timeout on SPY")

        vix_raw = _yf_download_with_timeout("^VIX", "6mo", _FETCH_TIMEOUT)
        if vix_raw is None:
            with _cache_lock:
                if _cached_result:
                    return dict(_cached_result, note="yfinance timeout on VIX — returning stale regime")
            return dict(_FALLBACK_REGIME, note="yfinance timeout on VIX")

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
        with _cache_lock:
            if _cached_result:
                return dict(_cached_result, note=f"fetch error ({e}) — returning stale regime")
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

        with _cache_lock:
            _cached_result = result
            _cached_at     = time.time()

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
