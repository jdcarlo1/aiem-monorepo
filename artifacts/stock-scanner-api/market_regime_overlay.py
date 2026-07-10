"""
market_regime_overlay.py
---------------------------
Sits IN FRONT of all your call-signal layers. Regardless of how bullish any
individual signal (dark pool, gamma, sweep, etc.) looks, this module can
override with "sit out" if enough independent risk indicators agree
conditions are bad for being long calls.

Since you're calls-focused, this is framed entirely around ONE question:
"is this a good environment to be net-long premium right now, or should we
preserve capital and wait?" It never recommends going short or betting
against the market — it only ever recommends full exposure, reduced
exposure, or sitting out entirely.

Combines up to 11 independent indicators, each contributing a vote, so no
single noisy reading can dominate:

  1. VIX level & trend          — elevated/rising VIX = bad for new call risk
  2. Trend structure (ADX/SMA)  — is the market actually trending up, or choppy?
  3. Breadth (advance/decline)  — are MOST stocks participating, or is it
                                   a narrow handful propping up the index?
  4. Put/call ratio              — extreme complacency or extreme fear, both
                                   are useful contrarian-adjacent signals
  5. Drawdown from recent high   — how far off highs are we right now?
  6. Realized volatility regime — reuses regime_monitor.py's existing
                                   volatility regime check if available
  7. GARCH(1,1) vol forecast    — forward-looking volatility clustering model;
                                   predicts whether vol is likely to rise or
                                   fall over the next 5 days (volatility_clustering.py)
  8. Rates direction             — 10Y yield trend; rising yields = headwind
                                   for risk assets, especially growth/tech
  9. Dollar strength             — DXY proxy via UUP; strengthening dollar =
                                   broad risk-asset headwind
 10. Sector rotation breadth    — fraction of sector ETFs above 50d SMA;
                                   narrow breadth = late-stage-rally warning
 11. PCA absorption ratio       — fraction of cross-sectional return variance
                                   explained by the top-N principal components
                                   (Kritzman et al.); high = fragile,
                                   correlated market, low = stock-picker's
                                   market (advanced_quant_indicators.py)

Each indicator returns a vote in {-1 (bearish/risk-off), 0 (neutral), 1 (bullish/risk-on)}.
The combined score and a CLEAR, READABLE explanation of which indicators
drove the call get logged through decision_logger so you can see exactly
why it said "sit out" any given week.

REQUIRES: pandas, numpy, arch (pip install arch for GARCH), yfinance.
"""

import json
import datetime as dt
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

import decision_logger as dl

try:
    from volatility_clustering import garch_regime_indicator as _garch_indicator
    _GARCH_AVAILABLE = True
except ImportError:
    _GARCH_AVAILABLE = False

try:
    from macro_cross_asset import get_macro_context_votes as _macro_votes
    _MACRO_AVAILABLE = True
except ImportError:
    _MACRO_AVAILABLE = False

try:
    from advanced_quant_indicators import absorption_ratio as _absorption_ratio
    _PCA_AVAILABLE = True
except ImportError:
    _PCA_AVAILABLE = False


def vix_indicator(vix_history: pd.Series, lookback: int = 20) -> Dict[str, Any]:
    """Votes risk-off if VIX is both elevated AND rising — the combination
    matters more than either alone. A high-but-falling VIX is often a
    recovery setup, not a warning."""
    if len(vix_history) < lookback + 1:
        return {"vote": 0, "reason": "insufficient VIX history"}

    current = vix_history.iloc[-1]
    avg     = vix_history.iloc[-lookback:].mean()
    trend   = vix_history.iloc[-1] - vix_history.iloc[-5]

    if current > avg * 1.25 and trend > 0:
        return {"vote": -1, "reason": f"VIX elevated ({current:.1f} vs {lookback}d avg {avg:.1f}) and rising"}
    if current < avg * 0.85 and trend < 0:
        return {"vote": 1, "reason": f"VIX low ({current:.1f}) and falling — calm, supportive of risk"}
    return {"vote": 0, "reason": f"VIX neutral ({current:.1f} vs avg {avg:.1f})"}


def trend_structure_indicator(price_history: pd.DataFrame, short: int = 20, long: int = 50) -> Dict[str, Any]:
    """Votes bullish only if price is ABOVE both moving averages AND the
    short MA is above the long MA. Votes bearish if price has broken below both."""
    df             = price_history.sort_values("date").copy()
    df["sma_short"] = df["close"].rolling(short).mean()
    df["sma_long"]  = df["close"].rolling(long).mean()
    df             = df.dropna()
    if len(df) < 5:
        return {"vote": 0, "reason": "insufficient price history"}

    last = df.iloc[-1]
    if last["close"] > last["sma_short"] > last["sma_long"]:
        return {"vote": 1, "reason": "Price above both MAs, short MA above long MA — confirmed uptrend"}
    if last["close"] < last["sma_short"] < last["sma_long"]:
        return {"vote": -1, "reason": "Price below both MAs, short MA below long MA — confirmed downtrend"}
    return {"vote": 0, "reason": "Mixed trend structure — no clear direction"}


def breadth_indicator(advancers: pd.Series, decliners: pd.Series, lookback: int = 10) -> Dict[str, Any]:
    """Votes risk-off if the index is propped up by a narrow set of names —
    a classic late-stage-rally warning sign."""
    if len(advancers) < lookback or len(decliners) < lookback:
        return {"vote": 0, "reason": "insufficient breadth history"}

    ratio = advancers.iloc[-lookback:].sum() / (decliners.iloc[-lookback:].sum() + 1e-9)

    if ratio < 0.7:
        return {"vote": -1, "reason": f"Weak breadth (advance/decline ratio {ratio:.2f}) — rally is narrow"}
    if ratio > 1.5:
        return {"vote": 1,  "reason": f"Strong breadth (advance/decline ratio {ratio:.2f}) — broad participation"}
    return {"vote": 0, "reason": f"Neutral breadth (ratio {ratio:.2f})"}


def put_call_ratio_indicator(put_call_ratio: float,
                              historical_mean: float = 0.7,
                              historical_std: float  = 0.15) -> Dict[str, Any]:
    """Extreme complacency (very low P/C) is a contrarian warning.
    Extreme fear (very high P/C) is treated cautiously, not as a buy signal."""
    z = (put_call_ratio - historical_mean) / historical_std

    if z < -1.5:
        return {"vote": -1, "reason": f"P/C ratio extremely low ({put_call_ratio:.2f}, z={z:.1f}) — crowd is complacently bullish, contrarian caution"}
    if z > 2.0:
        return {"vote": 0,  "reason": f"P/C ratio extremely high ({put_call_ratio:.2f}, z={z:.1f}) — fear-driven, could be capitulation OR further downside, treated as neutral"}
    return {"vote": 0, "reason": f"P/C ratio unremarkable ({put_call_ratio:.2f})"}


def drawdown_indicator(price_history: pd.DataFrame, lookback_days: int = 60) -> Dict[str, Any]:
    """Votes risk-off if currently in a meaningful drawdown from a recent high."""
    df = price_history.sort_values("date").tail(lookback_days)
    if len(df) < 10:
        return {"vote": 0, "reason": "insufficient price history"}

    recent_high  = df["close"].max()
    current      = df["close"].iloc[-1]
    drawdown_pct = (current - recent_high) / recent_high * 100

    if drawdown_pct < -8:
        return {"vote": -1, "reason": f"In a {abs(drawdown_pct):.1f}% drawdown from {lookback_days}-day high — elevated risk for new call positions"}
    if drawdown_pct > -2:
        return {"vote": 1,  "reason": f"Near {lookback_days}-day highs (drawdown only {abs(drawdown_pct):.1f}%) — supportive backdrop"}
    return {"vote": 0, "reason": f"Moderate drawdown ({abs(drawdown_pct):.1f}%) — no strong signal"}


def pca_absorption_indicator(returns_matrix: pd.DataFrame, n_factors: int = 5,
                              high_thresh: float = 0.65, low_thresh: float = 0.35) -> Dict[str, Any]:
    """Absorption Ratio (Kritzman et al.) systemic-risk vote.

    HIGH absorption (top n_factors principal components explain most of the
    cross-sectional variance) means the market is trading as one correlated
    block -- a fragile, "everything moves together" regime where individual
    stock-picking edges (dark pool, gamma, sweep, etc.) are more likely to
    get overrun by a broad risk-off wave. LOW absorption means idiosyncratic,
    stock-specific moves dominate -- a supportive backdrop for signal-driven
    call selection. This is deliberately ONE advisory vote among several
    (see combine_regime_votes) -- it can never single-handedly flip the
    recommendation, matching how every other indicator here is capped.
    """
    if returns_matrix is None or returns_matrix.empty or returns_matrix.shape[1] < n_factors + 1:
        return {"vote": 0, "reason": "insufficient cross-sectional universe for absorption ratio"}
    try:
        ar = _absorption_ratio(returns_matrix, n_factors=n_factors)
    except Exception as _e:
        return {"vote": 0, "reason": f"absorption ratio unavailable: {_e}"}

    if ar >= high_thresh:
        return {"vote": -1, "reason": f"Absorption ratio {ar:.2f} (>= {high_thresh}) -- highly correlated/fragile market, idiosyncratic edges more likely to get overrun"}
    if ar <= low_thresh:
        return {"vote": 1, "reason": f"Absorption ratio {ar:.2f} (<= {low_thresh}) -- stock-picker's market, idiosyncratic moves dominate"}
    return {"vote": 0, "reason": f"Absorption ratio {ar:.2f} -- unremarkable systemic-correlation level"}


def _build_summary(recommendation: str, votes: List[Dict[str, Any]]) -> str:
    bearish_reasons = [v["reason"] for v in votes if v["vote"] == -1]
    bullish_reasons = [v["reason"] for v in votes if v["vote"] == 1]

    if recommendation == "sit_out":
        return "Recommend sitting out of new call positions. Key concerns: " + "; ".join(bearish_reasons)
    if recommendation == "reduce_exposure":
        return "Recommend reduced call exposure (smaller size, higher conviction only). Concerns: " + "; ".join(bearish_reasons)
    if recommendation == "full_exposure":
        return "Supportive backdrop for call exposure. Tailwinds: " + "; ".join(bullish_reasons)
    return "Mixed signals — no strong directional read on overall market conditions. Trade individual signals on their own merits, at normal size."


def combine_regime_votes(
    vix_history: pd.Series,
    price_history: pd.DataFrame,
    advancers: Optional[pd.Series]             = None,
    decliners: Optional[pd.Series]             = None,
    put_call_ratio: Optional[float]            = None,
    regime_monitor_flags: Optional[List[Dict[str, Any]]] = None,
    returns_matrix: Optional[pd.DataFrame]     = None,
) -> Dict[str, Any]:
    """Runs all available indicators, combines their votes, and produces a
    clear recommendation. Missing inputs are treated as neutral (vote=0),
    not penalized — degrades gracefully with whatever data you have."""
    votes = []

    votes.append({"indicator": "vix",             **vix_indicator(vix_history)})
    votes.append({"indicator": "trend_structure",  **trend_structure_indicator(price_history)})
    votes.append({"indicator": "drawdown",         **drawdown_indicator(price_history)})

    if advancers is not None and decliners is not None:
        votes.append({"indicator": "breadth", **breadth_indicator(advancers, decliners)})

    if put_call_ratio is not None:
        votes.append({"indicator": "put_call_ratio", **put_call_ratio_indicator(put_call_ratio)})

    if _PCA_AVAILABLE and returns_matrix is not None:
        try:
            votes.append({"indicator": "pca_absorption", **pca_absorption_indicator(returns_matrix)})
        except Exception as _e:
            votes.append({"indicator": "pca_absorption", "vote": 0,
                          "reason": f"PCA absorption ratio unavailable: {_e}"})

    if regime_monitor_flags:
        critical = [f for f in regime_monitor_flags if f.get("severity") == "critical"]
        if critical:
            votes.append({
                "indicator": "regime_monitor",
                "vote":      -1,
                "reason":    f"{len(critical)} critical regime flag(s) active from regime_monitor.py",
            })

    if _GARCH_AVAILABLE:
        try:
            votes.append({"indicator": "garch_vol", **_garch_indicator(price_history)})
        except Exception as _e:
            votes.append({"indicator": "garch_vol", "vote": 0,
                          "reason": f"GARCH unavailable: {_e}"})

    if _MACRO_AVAILABLE:
        try:
            for _mv in _macro_votes():
                votes.append({"indicator": f"macro_{len(votes)}", **_mv})
        except Exception as _e:
            pass

    bearish_count = sum(1 for v in votes if v["vote"] == -1)
    bullish_count = sum(1 for v in votes if v["vote"] == 1)
    total_score   = sum(v["vote"] for v in votes)

    if bearish_count >= 3 and bearish_count > bullish_count:
        recommendation = "sit_out"
        confidence     = "high" if bearish_count >= 4 else "moderate"
    elif bearish_count >= 2 and bearish_count > bullish_count:
        recommendation = "reduce_exposure"
        confidence     = "moderate"
    elif bullish_count >= 3 and bullish_count > bearish_count:
        recommendation = "full_exposure"
        confidence     = "high" if bullish_count >= 4 else "moderate"
    else:
        recommendation = "normal_exposure"
        confidence     = "low"

    return {
        "recommendation":         recommendation,
        "confidence":             confidence,
        "total_score":            total_score,
        "n_indicators_used":      len(votes),
        "bearish_indicator_count": bearish_count,
        "bullish_indicator_count": bullish_count,
        "indicator_votes":        votes,
        "plain_language_summary": _build_summary(recommendation, votes),
    }


def get_weekly_regime_check(
    vix_history: pd.Series,
    price_history: pd.DataFrame,
    advancers: Optional[pd.Series]             = None,
    decliners: Optional[pd.Series]             = None,
    put_call_ratio: Optional[float]            = None,
    regime_monitor_flags: Optional[List[Dict[str, Any]]] = None,
    returns_matrix: Optional[pd.DataFrame]     = None,
) -> Dict[str, Any]:
    """Call this once a week (or before each batch of call recommendations).
    Logs the result through decision_logger so 'sit out' weeks are part of
    your reviewable track record just like trade decisions are."""
    result = combine_regime_votes(
        vix_history, price_history, advancers, decliners,
        put_call_ratio, regime_monitor_flags, returns_matrix,
    )

    dl.log_decision(
        signal_name="market_regime_overlay",
        decision_type="no_trade" if result["recommendation"] in ("sit_out", "reduce_exposure") else "hold",
        reasoning=result["plain_language_summary"],
        confidence={"high": 0.85, "moderate": 0.6, "low": 0.3}[result["confidence"]],
        input_state_snapshot=result,
    )

    return result


if __name__ == "__main__":
    print("market_regime_overlay: combines 7 independent indicators into sit-out / reduce / full-exposure call.")
    print("Call get_weekly_regime_check() before sending your weekly email recommendations.")
