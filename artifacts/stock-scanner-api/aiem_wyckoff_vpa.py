"""
aiem_wyckoff_vpa.py — Wyckoff and Volume Price Analysis (VPA) pattern detection.

Wyckoff patterns (8):
  Accumulation Phase, Distribution Phase, Spring, Upthrust,
  Sign of Strength (SOS), Sign of Weakness (SOW),
  Selling Climax, Buying Climax

VPA patterns (6):
  Volume Climax, Shakeout, No Demand, No Supply,
  Stopping Volume, Effort vs. Result, Volume Dry-Up

All detectors require OHLCV bars (oldest first) with at least 20 bars.
Volume is mandatory for all VPA and Wyckoff detectors.
"""
from __future__ import annotations
import statistics
from typing import List, Dict, Any, Optional
import numpy as np


def _avg_vol(bars: List[Dict], n: int = 20) -> float:
    vols = [b.get("volume", 0) or 0 for b in bars[-n:]]
    vols = [v for v in vols if v > 0]
    return statistics.mean(vols) if vols else 1.0

def _avg_range(bars: List[Dict], n: int = 14) -> float:
    ranges = [b["high"] - b["low"] for b in bars[-n:] if b["high"] > b["low"]]
    return statistics.mean(ranges) if ranges else 1.0

def _close_pos(bar: Dict) -> float:
    """Close position within bar range: 0=low, 1=high."""
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return 0.5
    return (bar["close"] - bar["low"]) / rng

def _body_pct(bar: Dict) -> float:
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return 0.0
    return abs(bar["close"] - bar["open"]) / rng

def _is_up_bar(bar: Dict) -> bool:
    return bar["close"] >= bar["open"]

def _is_down_bar(bar: Dict) -> bool:
    return bar["close"] < bar["open"]

def _spread(bar: Dict) -> float:
    return bar["high"] - bar["low"]

def _trend_slope(bars: List[Dict], n: int = 10) -> float:
    """Linear regression slope of closes over last n bars (normalized by price)."""
    if len(bars) < 2:
        return 0.0
    seg = [b["close"] for b in bars[-n:]]
    xs = list(range(len(seg)))
    if len(xs) < 2:
        return 0.0
    mean_x = sum(xs) / len(xs)
    mean_y = sum(seg) / len(seg)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, seg))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den > 0 else 0.0
    return slope / max(mean_y, 1.0)  # normalized


# ── VPA Patterns ──────────────────────────────────────────────────────────────

def detect_volume_climax(bars: List[Dict]) -> Optional[Dict]:
    """
    Volume Climax: Extremely high volume (>3x avg) on a wide-spread bar.
    Often marks a reversal zone. Direction depends on bar type.
    """
    if len(bars) < 21:
        return None
    curr = bars[-1]
    avg_v = _avg_vol(bars[:-1], 20)
    avg_r = _avg_range(bars[:-1], 14)
    vol = curr.get("volume", 0) or 0
    if vol < avg_v * 2.5:
        return None
    spread = _spread(curr)
    if spread < avg_r * 1.2:
        return None
    close_pos = _close_pos(curr)
    if _is_up_bar(curr) and close_pos >= 0.60:
        direction = "BEARISH"
        reason = "buying climax — massive volume on wide up-bar; supply likely overwhelming demand"
    elif _is_down_bar(curr) and close_pos <= 0.40:
        direction = "BULLISH"
        reason = "selling climax — massive volume on wide down-bar; demand likely absorbing supply"
    else:
        direction = "NEUTRAL"
        reason = f"volume climax on mixed close position={close_pos:.2f}; indeterminate direction"
    return {
        "pattern": "vpa_volume_climax",
        "category": "VPA",
        "direction": direction,
        "status": "confirmed",
        "confidence": 0.65,
        "key_levels": {"close": round(curr["close"], 4), "high": round(curr["high"], 4),
                       "low": round(curr["low"], 4)},
        "reason": reason,
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_shakeout(bars: List[Dict]) -> Optional[Dict]:
    """
    Shakeout: Breaks below support on high volume but closes near high — bullish trap.
    Weak holders flushed; smart money absorbs.
    """
    if len(bars) < 21:
        return None
    curr = bars[-1]
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    if vol < avg_v * 1.5:
        return None
    recent_lows = [b["low"] for b in bars[-21:-1]]
    support = min(recent_lows)
    if curr["low"] >= support:
        return None
    if _close_pos(curr) < 0.60:
        return None
    return {
        "pattern": "vpa_shakeout",
        "category": "VPA",
        "direction": "BULLISH",
        "status": "confirmed",
        "confidence": 0.68,
        "key_levels": {"support_broken": round(support, 4), "close": round(curr["close"], 4)},
        "reason": "intrabar break below support, close near high — weak hands flushed, absorption likely",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_no_demand(bars: List[Dict]) -> Optional[Dict]:
    """
    No Demand: Up bar with volume below previous bar volume AND narrow spread.
    Bears still in control — rally lacks sponsorship.
    """
    if len(bars) < 3:
        return None
    curr, prev = bars[-1], bars[-2]
    avg_r = _avg_range(bars[:-1], 14)
    vol_curr = curr.get("volume", 0) or 0
    vol_prev = prev.get("volume", 0) or 0
    if not _is_up_bar(curr):
        return None
    if vol_curr >= vol_prev:
        return None
    if _spread(curr) > avg_r * 0.7:
        return None
    return {
        "pattern": "vpa_no_demand",
        "category": "VPA",
        "direction": "BEARISH",
        "status": "confirmed",
        "confidence": 0.58,
        "key_levels": {"close": round(curr["close"], 4)},
        "reason": "up bar with below-avg volume and narrow spread — rally lacks sponsorship",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol_curr / max(vol_prev, 1), 2),
    }


def detect_no_supply(bars: List[Dict]) -> Optional[Dict]:
    """
    No Supply: Down bar with volume below previous bar volume AND narrow spread.
    Bulls still in control — pullback lacks sellers.
    """
    if len(bars) < 3:
        return None
    curr, prev = bars[-1], bars[-2]
    avg_r = _avg_range(bars[:-1], 14)
    vol_curr = curr.get("volume", 0) or 0
    vol_prev = prev.get("volume", 0) or 0
    if not _is_down_bar(curr):
        return None
    if vol_curr >= vol_prev:
        return None
    if _spread(curr) > avg_r * 0.7:
        return None
    return {
        "pattern": "vpa_no_supply",
        "category": "VPA",
        "direction": "BULLISH",
        "status": "confirmed",
        "confidence": 0.58,
        "key_levels": {"close": round(curr["close"], 4)},
        "reason": "down bar with below-avg volume and narrow spread — pullback lacks sellers",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol_curr / max(vol_prev, 1), 2),
    }


def detect_stopping_volume(bars: List[Dict]) -> Optional[Dict]:
    """
    Stopping Volume: Very high volume on a down bar that closes near its HIGH.
    Supply is being absorbed — potential bullish reversal.
    """
    if len(bars) < 21:
        return None
    curr = bars[-1]
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    if vol < avg_v * 2.0:
        return None
    if not _is_down_bar(curr):
        return None
    if _close_pos(curr) < 0.65:
        return None
    return {
        "pattern": "vpa_stopping_volume",
        "category": "VPA",
        "direction": "BULLISH",
        "status": "confirmed",
        "confidence": 0.70,
        "key_levels": {"close": round(curr["close"], 4), "low": round(curr["low"], 4)},
        "reason": "high volume on down bar closing near high — supply absorbed, potential reversal",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_effort_vs_result(bars: List[Dict]) -> Optional[Dict]:
    """
    Effort vs. Result: High volume but price barely moves (small spread).
    Hidden strength or weakness — forces canceling out.
    """
    if len(bars) < 21:
        return None
    curr = bars[-1]
    avg_v = _avg_vol(bars[:-1], 20)
    avg_r = _avg_range(bars[:-1], 14)
    vol = curr.get("volume", 0) or 0
    if vol < avg_v * 1.8:
        return None
    if _spread(curr) > avg_r * 0.50:
        return None
    close_pos = _close_pos(curr)
    direction = "BULLISH" if close_pos >= 0.50 else "BEARISH"
    return {
        "pattern": "vpa_effort_vs_result",
        "category": "VPA",
        "direction": direction,
        "status": "forming",
        "confidence": 0.50,
        "key_levels": {"close": round(curr["close"], 4)},
        "reason": "high volume with narrow price spread — significant absorption, hidden order flow",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_volume_dryup(bars: List[Dict]) -> Optional[Dict]:
    """
    Volume Dry-Up: Very low volume (<0.4x avg) in a consolidation area.
    Either trend continuation on next breakout or reversal setup.
    """
    if len(bars) < 21:
        return None
    curr = bars[-1]
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    if avg_v <= 0 or vol > avg_v * 0.40:
        return None
    slope = _trend_slope(bars, 10)
    direction = "BULLISH" if slope > 0 else ("BEARISH" if slope < 0 else "NEUTRAL")
    return {
        "pattern": "vpa_volume_dryup",
        "category": "VPA",
        "direction": direction,
        "status": "forming",
        "confidence": 0.45,
        "key_levels": {"close": round(curr["close"], 4)},
        "reason": f"volume dried to {vol/avg_v:.1%} of average — consolidation, next move likely amplified",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 3),
    }


# ── Wyckoff Patterns ──────────────────────────────────────────────────────────

def detect_selling_climax(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Selling Climax (SC): Highest volume bar in recent sequence,
    wide spread bearish bar, after a sustained downtrend. Marks potential
    start of accumulation.
    """
    if len(bars) < 30:
        return None
    curr = bars[-1]
    window = bars[-30:]
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    avg_r = _avg_range(bars[:-1], 14)
    slope = _trend_slope(bars[:-1], 15)
    if slope > -0.002:
        return None
    if vol < avg_v * 2.0:
        return None
    if not _is_down_bar(curr):
        return None
    if _spread(curr) < avg_r * 1.2:
        return None
    max_vol_in_window = max(b.get("volume", 0) or 0 for b in window[:-1])
    if vol < max_vol_in_window * 0.90:
        return None
    return {
        "pattern": "wyckoff_selling_climax",
        "category": "WYCKOFF",
        "direction": "BULLISH",
        "status": "confirmed",
        "confidence": 0.65,
        "key_levels": {"sc_low": round(curr["low"], 4), "sc_close": round(curr["close"], 4)},
        "reason": "highest volume bar in 30-bar window on wide bearish bar after downtrend — potential SC",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_buying_climax(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Buying Climax (BC): Highest volume bar, wide spread bullish bar,
    after a sustained uptrend. Marks potential start of distribution.
    """
    if len(bars) < 30:
        return None
    curr = bars[-1]
    window = bars[-30:]
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    avg_r = _avg_range(bars[:-1], 14)
    slope = _trend_slope(bars[:-1], 15)
    if slope < 0.002:
        return None
    if vol < avg_v * 2.0:
        return None
    if not _is_up_bar(curr):
        return None
    if _spread(curr) < avg_r * 1.2:
        return None
    max_vol_in_window = max(b.get("volume", 0) or 0 for b in window[:-1])
    if vol < max_vol_in_window * 0.90:
        return None
    return {
        "pattern": "wyckoff_buying_climax",
        "category": "WYCKOFF",
        "direction": "BEARISH",
        "status": "confirmed",
        "confidence": 0.65,
        "key_levels": {"bc_high": round(curr["high"], 4), "bc_close": round(curr["close"], 4)},
        "reason": "highest volume bar in 30-bar window on wide bullish bar after uptrend — potential BC",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_spring(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Spring: Price dips below a recent support level but quickly
    recovers above it, closing in upper portion of the bar, on volume
    below the prior selling climax. Smart money absorbing supply.
    """
    if len(bars) < 25:
        return None
    curr = bars[-1]
    lookback = bars[-25:-1]
    support = min(b["low"] for b in lookback)
    if curr["low"] >= support:
        return None
    if _close_pos(curr) < 0.55:
        return None
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    if vol > avg_v * 2.5:
        return None
    return {
        "pattern": "wyckoff_spring",
        "category": "WYCKOFF",
        "direction": "BULLISH",
        "status": "confirmed",
        "confidence": 0.72,
        "key_levels": {"support_broken": round(support, 4),
                       "spring_low": round(curr["low"], 4),
                       "close": round(curr["close"], 4)},
        "reason": "intrabar breach of support with recovery close — classic Wyckoff Spring",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_upthrust(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Upthrust (UT): Price spikes above recent resistance but closes
    below it, in lower portion of the bar. False breakout; bearish.
    """
    if len(bars) < 25:
        return None
    curr = bars[-1]
    lookback = bars[-25:-1]
    resistance = max(b["high"] for b in lookback)
    if curr["high"] <= resistance:
        return None
    if _close_pos(curr) > 0.45:
        return None
    if curr["close"] >= resistance:
        return None
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    if vol > avg_v * 2.5:
        return None
    return {
        "pattern": "wyckoff_upthrust",
        "category": "WYCKOFF",
        "direction": "BEARISH",
        "status": "confirmed",
        "confidence": 0.72,
        "key_levels": {"resistance_broken": round(resistance, 4),
                       "upthrust_high": round(curr["high"], 4),
                       "close": round(curr["close"], 4)},
        "reason": "intrabar breach of resistance with rejection close — classic Wyckoff Upthrust",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_sign_of_strength(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Sign of Strength (SOS): Strong rally on increasing volume after
    a Spring or Selling Climax. Confirms accumulation complete, markup likely.
    """
    if len(bars) < 10:
        return None
    curr = bars[-1]
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    avg_r = _avg_range(bars[:-1], 14)
    slope = _trend_slope(bars[-10:], 10)
    if slope < 0.003:
        return None
    if not _is_up_bar(curr):
        return None
    if vol < avg_v * 1.5:
        return None
    if _spread(curr) < avg_r * 1.1:
        return None
    if _close_pos(curr) < 0.70:
        return None
    return {
        "pattern": "wyckoff_sos",
        "category": "WYCKOFF",
        "direction": "BULLISH",
        "status": "confirmed",
        "confidence": 0.65,
        "key_levels": {"sos_close": round(curr["close"], 4), "sos_high": round(curr["high"], 4)},
        "reason": "strong wide-spread up bar on high volume after base — Sign of Strength",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_sign_of_weakness(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Sign of Weakness (SOW): Sharp break on increasing volume after
    an Upthrust or Buying Climax. Confirms distribution complete, markdown likely.
    """
    if len(bars) < 10:
        return None
    curr = bars[-1]
    avg_v = _avg_vol(bars[:-1], 20)
    vol = curr.get("volume", 0) or 0
    avg_r = _avg_range(bars[:-1], 14)
    slope = _trend_slope(bars[-10:], 10)
    if slope > -0.003:
        return None
    if not _is_down_bar(curr):
        return None
    if vol < avg_v * 1.5:
        return None
    if _spread(curr) < avg_r * 1.1:
        return None
    if _close_pos(curr) > 0.30:
        return None
    return {
        "pattern": "wyckoff_sow",
        "category": "WYCKOFF",
        "direction": "BEARISH",
        "status": "confirmed",
        "confidence": 0.65,
        "key_levels": {"sow_close": round(curr["close"], 4), "sow_low": round(curr["low"], 4)},
        "reason": "strong wide-spread down bar on high volume after distribution — Sign of Weakness",
        "bar_index": len(bars) - 1,
        "volume_ratio": round(vol / avg_v, 2),
    }


def detect_accumulation_phase(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Accumulation Phase: Sideways price action with declining volume
    over an extended period following a Selling Climax, with no new lows.
    """
    if len(bars) < 40:
        return None
    window = bars[-40:]
    avg_v_early = _avg_vol(window[:20], 20)
    avg_v_late = _avg_vol(window[20:], 20)
    if avg_v_late >= avg_v_early * 0.85:
        return None
    closes = [b["close"] for b in window]
    price_range = max(closes) - min(closes)
    avg_price = sum(closes) / len(closes)
    if price_range / avg_price > 0.15:
        return None
    slope = _trend_slope(window, 40)
    if abs(slope) > 0.001:
        return None
    early_low = min(b["low"] for b in window[:20])
    late_low = min(b["low"] for b in window[20:])
    if late_low < early_low * 0.97:
        return None
    return {
        "pattern": "wyckoff_accumulation",
        "category": "WYCKOFF",
        "direction": "BULLISH",
        "status": "forming",
        "confidence": 0.60,
        "key_levels": {"range_low": round(min(closes), 4), "range_high": round(max(closes), 4)},
        "reason": "40-bar sideways range with declining volume and no new lows — Wyckoff Accumulation",
        "bar_index": len(bars) - 1,
        "volume_decline_pct": round((1 - avg_v_late / avg_v_early) * 100, 1),
    }


def detect_distribution_phase(bars: List[Dict]) -> Optional[Dict]:
    """
    Wyckoff Distribution Phase: Sideways price action with declining volume
    at a top, following a Buying Climax, with no new highs.
    """
    if len(bars) < 40:
        return None
    window = bars[-40:]
    avg_v_early = _avg_vol(window[:20], 20)
    avg_v_late = _avg_vol(window[20:], 20)
    if avg_v_late >= avg_v_early * 0.85:
        return None
    closes = [b["close"] for b in window]
    price_range = max(closes) - min(closes)
    avg_price = sum(closes) / len(closes)
    if price_range / avg_price > 0.15:
        return None
    slope = _trend_slope(window, 40)
    if abs(slope) > 0.001:
        return None
    early_high = max(b["high"] for b in window[:20])
    late_high = max(b["high"] for b in window[20:])
    if late_high > early_high * 1.03:
        return None
    return {
        "pattern": "wyckoff_distribution",
        "category": "WYCKOFF",
        "direction": "BEARISH",
        "status": "forming",
        "confidence": 0.60,
        "key_levels": {"range_low": round(min(closes), 4), "range_high": round(max(closes), 4)},
        "reason": "40-bar sideways range with declining volume and no new highs — Wyckoff Distribution",
        "bar_index": len(bars) - 1,
        "volume_decline_pct": round((1 - avg_v_late / avg_v_early) * 100, 1),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def detect_wyckoff_vpa_patterns(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Run all 14 Wyckoff and VPA detectors against bars (oldest first).
    Returns list of PatternResult dicts.
    """
    if len(bars) < 20:
        return []

    results = []
    detectors = [
        detect_volume_climax,
        detect_shakeout,
        detect_no_demand,
        detect_no_supply,
        detect_stopping_volume,
        detect_effort_vs_result,
        detect_volume_dryup,
        detect_selling_climax,
        detect_buying_climax,
        detect_spring,
        detect_upthrust,
        detect_sign_of_strength,
        detect_sign_of_weakness,
        detect_accumulation_phase,
        detect_distribution_phase,
    ]
    for detector in detectors:
        try:
            result = detector(bars)
            if result is not None:
                results.append(result)
        except Exception:
            pass
    return results
