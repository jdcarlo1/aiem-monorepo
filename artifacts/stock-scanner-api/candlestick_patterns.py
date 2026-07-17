"""
candlestick_patterns.py — Complete candlestick pattern library.
50 patterns: single-candle, two-candle, three-candle, multi-candle.
All detectors operate on list of OHLCV dicts (oldest first):
  {"open": float, "high": float, "low": float, "close": float, "volume": float}
detect_patterns() returns a list of PatternResult dicts.
"""
from __future__ import annotations
import datetime as dt
from typing import List, Dict, Any, Optional
import psycopg2
import psycopg2.extras


def _body(o: float, c: float) -> float:
    return abs(c - o)

def _range(h: float, l: float) -> float:
    return h - l if h > l else 1e-9

def _upper_shadow(o: float, h: float, c: float) -> float:
    return h - max(o, c)

def _lower_shadow(o: float, l: float, c: float) -> float:
    return min(o, c) - l

def _gaps_up(prev: Dict, curr: Dict) -> bool:
    return curr["low"] > prev["high"]

def _gaps_down(prev: Dict, curr: Dict) -> bool:
    return curr["high"] < prev["low"]

def _preceding_trend(bars: List[Dict], lookback: int = 5) -> str:
    ctx = bars[-(lookback + 1):-1]
    if len(ctx) < 2:
        return "flat"
    net = ctx[-1]["close"] - ctx[0]["close"]
    if net > 0:
        return "uptrend"
    if net < 0:
        return "downtrend"
    return "flat"


# ── Single-candle ─────────────────────────────────────────────────────────────

def is_doji(o: float, h: float, l: float, c: float, threshold: float = 0.1) -> bool:
    rng = _range(h, l)
    return _body(o, c) / rng <= threshold

def is_dragonfly_doji(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    return (_body(o, c) / rng <= 0.10
            and _lower_shadow(o, l, c) / rng >= 0.60
            and _upper_shadow(o, h, c) / rng <= 0.10)

def is_gravestone_doji(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    return (_body(o, c) / rng <= 0.10
            and _upper_shadow(o, h, c) / rng >= 0.60
            and _lower_shadow(o, l, c) / rng <= 0.10)

def is_long_legged_doji(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    return (_body(o, c) / rng <= 0.10
            and _upper_shadow(o, h, c) / rng >= 0.30
            and _lower_shadow(o, l, c) / rng >= 0.30)

def is_hammer(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    body = _body(o, c)
    lower = _lower_shadow(o, l, c)
    upper = _upper_shadow(o, h, c)
    return body / rng >= 0.10 and lower >= 2 * body and upper <= body

def is_inverted_hammer(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    body = _body(o, c)
    upper = _upper_shadow(o, h, c)
    lower = _lower_shadow(o, l, c)
    return body / rng >= 0.10 and upper >= 2 * body and lower <= body

def is_hanging_man(o: float, h: float, l: float, c: float, trend: str) -> bool:
    return trend == "uptrend" and is_hammer(o, h, l, c)

def is_shooting_star(o: float, h: float, l: float, c: float, trend: str = "uptrend") -> bool:
    rng = _range(h, l)
    body = _body(o, c)
    upper = _upper_shadow(o, h, c)
    lower = _lower_shadow(o, l, c)
    return (trend == "uptrend"
            and body / rng >= 0.05
            and upper >= 2 * body
            and lower <= body * 0.5)

def is_bullish_marubozu(o: float, h: float, l: float, c: float) -> bool:
    if c <= o:
        return False
    rng = _range(h, l)
    body = _body(o, c)
    return (body / rng >= 0.90
            and _upper_shadow(o, h, c) / rng <= 0.03
            and _lower_shadow(o, l, c) / rng <= 0.03)

def is_bearish_marubozu(o: float, h: float, l: float, c: float) -> bool:
    if c >= o:
        return False
    rng = _range(h, l)
    body = _body(o, c)
    return (body / rng >= 0.90
            and _upper_shadow(o, h, c) / rng <= 0.03
            and _lower_shadow(o, l, c) / rng <= 0.03)

def is_spinning_top(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    body = _body(o, c)
    upper = _upper_shadow(o, h, c)
    lower = _lower_shadow(o, l, c)
    return (body / rng <= 0.30
            and upper >= body * 0.8
            and lower >= body * 0.8)

def is_high_wave(o: float, h: float, l: float, c: float) -> bool:
    rng = _range(h, l)
    body = _body(o, c)
    upper = _upper_shadow(o, h, c)
    lower = _lower_shadow(o, l, c)
    return (body / rng <= 0.15
            and upper / rng >= 0.35
            and lower / rng >= 0.35)

def is_bullish_belt_hold(o: float, h: float, l: float, c: float, trend: str) -> bool:
    if c <= o:
        return False
    rng = _range(h, l)
    return (trend == "downtrend"
            and _lower_shadow(o, l, c) / rng <= 0.03
            and _body(o, c) / rng >= 0.65)

def is_bearish_belt_hold(o: float, h: float, l: float, c: float, trend: str) -> bool:
    if c >= o:
        return False
    rng = _range(h, l)
    return (trend == "uptrend"
            and _upper_shadow(o, h, c) / rng <= 0.03
            and _body(o, c) / rng >= 0.65)


# ── Two-candle ────────────────────────────────────────────────────────────────

def is_bullish_engulfing(prev: Dict, curr: Dict) -> bool:
    return (prev["close"] < prev["open"]
            and curr["close"] > curr["open"]
            and curr["open"] <= prev["close"]
            and curr["close"] >= prev["open"])

def is_bearish_engulfing(prev: Dict, curr: Dict) -> bool:
    return (prev["close"] > prev["open"]
            and curr["close"] < curr["open"]
            and curr["open"] >= prev["close"]
            and curr["close"] <= prev["open"])

def is_piercing_line(prev: Dict, curr: Dict, trend: str) -> bool:
    if trend != "downtrend" or prev["close"] >= prev["open"] or curr["close"] <= curr["open"]:
        return False
    midpoint = (prev["open"] + prev["close"]) / 2
    return (curr["open"] < prev["close"]
            and curr["close"] > midpoint
            and curr["close"] < prev["open"])

def is_dark_cloud_cover(prev: Dict, curr: Dict, trend: str) -> bool:
    if trend != "uptrend" or prev["close"] <= prev["open"] or curr["close"] >= curr["open"]:
        return False
    midpoint = (prev["open"] + prev["close"]) / 2
    return (curr["open"] > prev["close"]
            and curr["close"] < midpoint
            and curr["close"] > prev["open"])

def is_bullish_harami(prev: Dict, curr: Dict, trend: str) -> bool:
    if trend != "downtrend":
        return False
    prev_body = _body(prev["open"], prev["close"])
    curr_body = _body(curr["open"], curr["close"])
    if prev_body <= 0:
        return False
    return (prev["close"] < prev["open"]
            and curr["close"] > curr["open"]
            and curr_body <= prev_body * 0.50
            and curr["open"] > prev["close"]
            and curr["close"] < prev["open"])

def is_bearish_harami(prev: Dict, curr: Dict, trend: str) -> bool:
    if trend != "uptrend":
        return False
    prev_body = _body(prev["open"], prev["close"])
    curr_body = _body(curr["open"], curr["close"])
    if prev_body <= 0:
        return False
    return (prev["close"] > prev["open"]
            and curr["close"] < curr["open"]
            and curr_body <= prev_body * 0.50
            and curr["open"] < prev["close"]
            and curr["close"] > prev["open"])

def is_bullish_harami_cross(prev: Dict, curr: Dict, trend: str) -> bool:
    return (trend == "downtrend"
            and prev["close"] < prev["open"]
            and is_doji(curr["open"], curr["high"], curr["low"], curr["close"])
            and curr["open"] > prev["close"]
            and curr["close"] < prev["open"])

def is_bearish_harami_cross(prev: Dict, curr: Dict, trend: str) -> bool:
    return (trend == "uptrend"
            and prev["close"] > prev["open"]
            and is_doji(curr["open"], curr["high"], curr["low"], curr["close"])
            and curr["open"] < prev["close"]
            and curr["close"] > prev["open"])

def is_tweezer_tops(prev: Dict, curr: Dict, trend: str) -> bool:
    if trend != "uptrend":
        return False
    tol = (prev["high"] + curr["high"]) / 2 * 0.003
    return (abs(prev["high"] - curr["high"]) <= tol
            and prev["close"] >= prev["open"]
            and curr["close"] < curr["open"])

def is_tweezer_bottoms(prev: Dict, curr: Dict, trend: str) -> bool:
    if trend != "downtrend":
        return False
    tol = (prev["low"] + curr["low"]) / 2 * 0.003
    return (abs(prev["low"] - curr["low"]) <= tol
            and prev["close"] <= prev["open"]
            and curr["close"] > curr["open"])

def is_bullish_kicker(prev: Dict, curr: Dict) -> bool:
    return (prev["close"] < prev["open"]
            and curr["close"] > curr["open"]
            and curr["open"] >= prev["open"]
            and _gaps_up(prev, curr))

def is_bearish_kicker(prev: Dict, curr: Dict) -> bool:
    return (prev["close"] > prev["open"]
            and curr["close"] < curr["open"]
            and curr["open"] <= prev["open"]
            and _gaps_down(prev, curr))

def is_on_neck(prev: Dict, curr: Dict) -> bool:
    tol = _range(prev["high"], prev["low"]) * 0.03
    return (prev["close"] < prev["open"]
            and curr["close"] > curr["open"]
            and curr["open"] < prev["low"]
            and abs(curr["close"] - prev["low"]) <= tol)

def is_in_neck(prev: Dict, curr: Dict) -> bool:
    tol = _range(prev["high"], prev["low"]) * 0.05
    return (prev["close"] < prev["open"]
            and curr["close"] > curr["open"]
            and curr["open"] < prev["low"]
            and abs(curr["close"] - prev["close"]) <= tol)

def is_matching_low(prev: Dict, curr: Dict, trend: str) -> bool:
    tol = (prev["close"] + curr["close"]) / 2 * 0.003
    return (trend == "downtrend"
            and prev["close"] < prev["open"]
            and curr["close"] < curr["open"]
            and abs(prev["close"] - curr["close"]) <= tol)


# ── Three-candle ──────────────────────────────────────────────────────────────

def is_morning_star(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    c1, c2, c3 = bars[-3], bars[-2], bars[-1]
    c1_bearish = c1["close"] < c1["open"]
    c1_body = _body(c1["open"], c1["close"])
    c2_body = _body(c2["open"], c2["close"])
    c3_bullish = c3["close"] > c3["open"]
    small_star = c1_body > 0 and c2_body < c1_body * 0.5
    deep_close = c3["close"] > (c1["open"] + c1["close"]) / 2
    return bool(c1_bearish and small_star and c3_bullish and deep_close)

def is_evening_star(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    c1, c2, c3 = bars[-3], bars[-2], bars[-1]
    c1_bullish = c1["close"] > c1["open"]
    c1_body = _body(c1["open"], c1["close"])
    c2_body = _body(c2["open"], c2["close"])
    c3_bearish = c3["close"] < c3["open"]
    small_star = c1_body > 0 and c2_body < c1_body * 0.5
    deep_close = c3["close"] < (c1["open"] + c1["close"]) / 2
    return bool(c1_bullish and small_star and c3_bearish and deep_close)

def is_morning_doji_star(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] < a["open"]
            and is_doji(b["open"], b["high"], b["low"], b["close"])
            and b["high"] < a["close"]
            and c["close"] > c["open"]
            and c["close"] > (a["open"] + a["close"]) / 2)

def is_evening_doji_star(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"]
            and is_doji(b["open"], b["high"], b["low"], b["close"])
            and b["low"] > a["close"]
            and c["close"] < c["open"]
            and c["close"] < (a["open"] + a["close"]) / 2)

def is_three_white_soldiers(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"] and b["close"] > b["open"] and c["close"] > c["open"]
            and b["open"] > a["open"] and b["open"] < a["close"]
            and c["open"] > b["open"] and c["open"] < b["close"]
            and c["close"] > b["close"] > a["close"]
            and _upper_shadow(c["open"], c["high"], c["close"]) < _body(c["open"], c["close"]) * 0.3)

def is_three_black_crows(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] < a["open"] and b["close"] < b["open"] and c["close"] < c["open"]
            and b["open"] < a["open"] and b["open"] > a["close"]
            and c["open"] < b["open"] and c["open"] > b["close"]
            and c["close"] < b["close"] < a["close"]
            and _lower_shadow(c["open"], c["low"], c["close"]) < _body(c["open"], c["close"]) * 0.3)

def is_three_inside_up(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] < a["open"]
            and is_bullish_harami(a, b, "downtrend")
            and c["close"] > c["open"]
            and c["close"] > a["open"])

def is_three_inside_down(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"]
            and is_bearish_harami(a, b, "uptrend")
            and c["close"] < c["open"]
            and c["close"] < a["open"])

def is_three_outside_up(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] < a["open"]
            and is_bullish_engulfing(a, b)
            and c["close"] > c["open"]
            and c["close"] > b["close"])

def is_three_outside_down(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"]
            and is_bearish_engulfing(a, b)
            and c["close"] < c["open"]
            and c["close"] < b["close"])

def is_abandoned_baby_bullish(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] < a["open"]
            and is_doji(b["open"], b["high"], b["low"], b["close"])
            and _gaps_down(a, b) and _gaps_up(b, c)
            and c["close"] > c["open"])

def is_abandoned_baby_bearish(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"]
            and is_doji(b["open"], b["high"], b["low"], b["close"])
            and _gaps_up(a, b) and _gaps_down(b, c)
            and c["close"] < c["open"])

def is_three_stars_south(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    a_rng = _range(a["high"], a["low"])
    b_rng = _range(b["high"], b["low"])
    c_rng = _range(c["high"], c["low"])
    return (a["close"] < a["open"] and b["close"] < b["open"] and c["close"] < c["open"]
            and a_rng > b_rng > c_rng
            and c["low"] >= b["low"]
            and is_bullish_marubozu(c["open"], c["high"], c["low"], c["close"]))

def is_advance_block(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    au = _upper_shadow(a["open"], a["high"], a["close"])
    bu = _upper_shadow(b["open"], b["high"], b["close"])
    cu = _upper_shadow(c["open"], c["high"], c["close"])
    return (a["close"] > a["open"] and b["close"] > b["open"] and c["close"] > c["open"]
            and b["close"] > a["close"] and c["close"] > b["close"]
            and cu > bu > au
            and cu > _body(c["open"], c["close"]) * 0.3)

def is_deliberation(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"] and b["close"] > b["open"]
            and b["close"] > a["close"]
            and _body(b["open"], b["close"]) > _body(a["open"], a["close"]) * 0.5
            and _body(c["open"], c["close"]) <= _body(b["open"], b["close"]) * 0.25
            and c["open"] >= b["close"] * 0.99)


# ── Multi-candle ──────────────────────────────────────────────────────────────

def is_rising_three_methods(bars: List[Dict]) -> bool:
    if len(bars) < 5:
        return False
    a, b, c, d, e = bars[-5], bars[-4], bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"]
            and _body(a["open"], a["close"]) > _body(b["open"], b["close"]) * 2
            and b["close"] < b["open"] and c["close"] < c["open"] and d["close"] < d["open"]
            and all(x["low"] > a["low"] and x["high"] < a["high"] for x in [b, c, d])
            and e["close"] > e["open"] and e["close"] > a["close"])

def is_falling_three_methods(bars: List[Dict]) -> bool:
    if len(bars) < 5:
        return False
    a, b, c, d, e = bars[-5], bars[-4], bars[-3], bars[-2], bars[-1]
    return (a["close"] < a["open"]
            and _body(a["open"], a["close"]) > _body(b["open"], b["close"]) * 2
            and b["close"] > b["open"] and c["close"] > c["open"] and d["close"] > d["open"]
            and all(x["high"] < a["high"] and x["low"] > a["low"] for x in [b, c, d])
            and e["close"] < e["open"] and e["close"] < a["close"])

def is_upside_gap_three_methods(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] > a["open"] and b["close"] > b["open"] and _gaps_up(a, b)
            and c["close"] < c["open"]
            and c["open"] >= b["close"] and a["open"] <= c["close"] <= a["close"])

def is_downside_gap_three_methods(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    return (a["close"] < a["open"] and b["close"] < b["open"] and _gaps_down(a, b)
            and c["close"] > c["open"]
            and c["open"] <= b["close"] and a["close"] <= c["close"] <= a["open"])

def is_stick_sandwich(bars: List[Dict]) -> bool:
    if len(bars) < 3:
        return False
    a, b, c = bars[-3], bars[-2], bars[-1]
    tol = (a["close"] + c["close"]) / 2 * 0.005
    return (a["close"] < a["open"]
            and b["close"] > b["open"]
            and c["close"] < c["open"]
            and abs(a["close"] - c["close"]) <= tol)

def is_concealing_baby_swallow(bars: List[Dict]) -> bool:
    if len(bars) < 4:
        return False
    a, b, c, d = bars[-4], bars[-3], bars[-2], bars[-1]
    return (is_bearish_marubozu(a["open"], a["high"], a["low"], a["close"])
            and is_bearish_marubozu(b["open"], b["high"], b["low"], b["close"])
            and b["open"] < a["close"]
            and c["close"] < c["open"] and c["high"] > b["close"]
            and d["close"] < d["open"] and d["high"] >= c["high"] and d["low"] <= b["low"])


# ── Main dispatcher ───────────────────────────────────────────────────────────

def detect_patterns(ohlc_bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Run all 50 candlestick detectors against ohlc_bars (oldest-first list).
    Each bar must have: open, high, low, close; volume is optional.
    Returns {"patterns": [...], "trend": str, "checked_at": str, "bar_close": float}.
    """
    if len(ohlc_bars) < 2:
        return {"patterns": [], "error": "need at least 2 bars"}

    results: List[Dict] = []
    trend = _preceding_trend(ohlc_bars)
    curr = ohlc_bars[-1]
    prev = ohlc_bars[-2]
    o, h, l, c = curr["open"], curr["high"], curr["low"], curr["close"]

    def _add(name: str, direction: str, confidence: float, reason: str):
        results.append({
            "pattern": name,
            "category": "CANDLESTICK",
            "direction": direction,
            "status": "confirmed",
            "confidence": round(confidence, 2),
            "reason": reason,
            "bar_index": len(ohlc_bars) - 1,
        })

    # Single-candle
    if is_dragonfly_doji(o, h, l, c):
        _add("dragonfly_doji", "BULLISH", 0.55, "doji with long lower shadow — buyers absorbed sellers")
    elif is_gravestone_doji(o, h, l, c):
        _add("gravestone_doji", "BEARISH", 0.55, "doji with long upper shadow — sellers rejected rally")
    elif is_long_legged_doji(o, h, l, c):
        _add("long_legged_doji", "NEUTRAL", 0.40, "extreme upper and lower shadows — high indecision")
    elif is_doji(o, h, l, c):
        _add("doji", "NEUTRAL", 0.40, "open ≈ close — indecision")
    if is_bullish_marubozu(o, h, l, c):
        _add("bullish_marubozu", "BULLISH", 0.65, "full-range white candle, no shadows — dominant buying")
    if is_bearish_marubozu(o, h, l, c):
        _add("bearish_marubozu", "BEARISH", 0.65, "full-range black candle, no shadows — dominant selling")
    if is_hammer(o, h, l, c) and trend == "downtrend":
        _add("hammer", "BULLISH", 0.60, "long lower shadow after downtrend — demand absorbed supply")
    if is_inverted_hammer(o, h, l, c) and trend == "downtrend":
        _add("inverted_hammer", "BULLISH", 0.50, "long upper shadow after downtrend — tentative buying")
    if is_hanging_man(o, h, l, c, trend):
        _add("hanging_man", "BEARISH", 0.55, "hammer shape after uptrend — exhaustion warning")
    if is_shooting_star(o, h, l, c, trend):
        _add("shooting_star", "BEARISH", 0.60, "long upper shadow after uptrend — sellers overpowered buyers")
    if is_spinning_top(o, h, l, c) and not is_doji(o, h, l, c):
        _add("spinning_top", "NEUTRAL", 0.35, "small body with equal shadows — balanced forces")
    if is_high_wave(o, h, l, c):
        _add("high_wave", "NEUTRAL", 0.35, "very long shadows, tiny body — extreme uncertainty")
    if is_bullish_belt_hold(o, h, l, c, trend):
        _add("bullish_belt_hold", "BULLISH", 0.55, "opens at low, closes high — strong demand off the open")
    if is_bearish_belt_hold(o, h, l, c, trend):
        _add("bearish_belt_hold", "BEARISH", 0.55, "opens at high, closes low — strong supply at the open")

    # Two-candle
    if is_bullish_engulfing(prev, curr):
        _add("bullish_engulfing", "BULLISH", 0.65, "white candle engulfs prior black candle")
    if is_bearish_engulfing(prev, curr):
        _add("bearish_engulfing", "BEARISH", 0.65, "black candle engulfs prior white candle")
    if is_piercing_line(prev, curr, trend):
        _add("piercing_line", "BULLISH", 0.60, "opens below prior close, closes above midpoint")
    if is_dark_cloud_cover(prev, curr, trend):
        _add("dark_cloud_cover", "BEARISH", 0.60, "opens above prior close, closes below midpoint")
    if is_bullish_harami(prev, curr, trend):
        _add("bullish_harami", "BULLISH", 0.50, "small white inside prior large black — slowing momentum")
    if is_bearish_harami(prev, curr, trend):
        _add("bearish_harami", "BEARISH", 0.50, "small black inside prior large white — slowing momentum")
    if is_bullish_harami_cross(prev, curr, trend):
        _add("bullish_harami_cross", "BULLISH", 0.55, "doji inside prior black candle after downtrend")
    if is_bearish_harami_cross(prev, curr, trend):
        _add("bearish_harami_cross", "BEARISH", 0.55, "doji inside prior white candle after uptrend")
    if is_tweezer_tops(prev, curr, trend):
        _add("tweezer_tops", "BEARISH", 0.55, "matching highs — resistance tested twice, failed")
    if is_tweezer_bottoms(prev, curr, trend):
        _add("tweezer_bottoms", "BULLISH", 0.55, "matching lows — support tested twice, held")
    if is_bullish_kicker(prev, curr):
        _add("bullish_kicker", "BULLISH", 0.75, "gap-up white candle after black — sharp sentiment reversal")
    if is_bearish_kicker(prev, curr):
        _add("bearish_kicker", "BEARISH", 0.75, "gap-down black candle after white — sharp sentiment reversal")
    if is_on_neck(prev, curr):
        _add("on_neck", "BEARISH", 0.45, "close near prior bar's low — bears remain in control")
    if is_in_neck(prev, curr):
        _add("in_neck", "BEARISH", 0.45, "close inside prior bar body — slight bullish failure")
    if is_matching_low(prev, curr, trend):
        _add("matching_low", "BULLISH", 0.50, "two equal lows — support confirmed")

    # Three-candle
    if is_morning_star(ohlc_bars):
        _add("morning_star", "BULLISH", 0.70, "black + small star + white closing above midpoint")
    if is_evening_star(ohlc_bars):
        _add("evening_star", "BEARISH", 0.70, "white + small star + black closing below midpoint")
    if is_morning_doji_star(ohlc_bars):
        _add("morning_doji_star", "BULLISH", 0.75, "black + doji + white — strongest bullish reversal")
    if is_evening_doji_star(ohlc_bars):
        _add("evening_doji_star", "BEARISH", 0.75, "white + doji + black — strongest bearish reversal")
    if is_three_white_soldiers(ohlc_bars):
        _add("three_white_soldiers", "BULLISH", 0.72, "three consecutive higher-closing white candles")
    if is_three_black_crows(ohlc_bars):
        _add("three_black_crows", "BEARISH", 0.72, "three consecutive lower-closing black candles")
    if is_three_inside_up(ohlc_bars):
        _add("three_inside_up", "BULLISH", 0.65, "harami confirmed by third bull bar")
    if is_three_inside_down(ohlc_bars):
        _add("three_inside_down", "BEARISH", 0.65, "bearish harami confirmed by third bear bar")
    if is_three_outside_up(ohlc_bars):
        _add("three_outside_up", "BULLISH", 0.68, "bullish engulfing confirmed by follow-through")
    if is_three_outside_down(ohlc_bars):
        _add("three_outside_down", "BEARISH", 0.68, "bearish engulfing confirmed by follow-through")
    if is_abandoned_baby_bullish(ohlc_bars):
        _add("abandoned_baby_bullish", "BULLISH", 0.78, "gap-down doji + gap-up white — rare strong reversal")
    if is_abandoned_baby_bearish(ohlc_bars):
        _add("abandoned_baby_bearish", "BEARISH", 0.78, "gap-up doji + gap-down black — rare strong reversal")
    if is_three_stars_south(ohlc_bars):
        _add("three_stars_south", "BULLISH", 0.60, "three shrinking bearish bars — selling exhausting")
    if is_advance_block(ohlc_bars):
        _add("advance_block", "BEARISH", 0.50, "three white soldiers with growing upper shadows — stalling")
    if is_deliberation(ohlc_bars):
        _add("deliberation", "BEARISH", 0.48, "two strong white then tiny candle near prior close")

    # Multi-candle
    if is_rising_three_methods(ohlc_bars):
        _add("rising_three_methods", "BULLISH", 0.65, "bull bar, three small bears inside, strong breakout")
    if is_falling_three_methods(ohlc_bars):
        _add("falling_three_methods", "BEARISH", 0.65, "bear bar, three small bulls inside, strong breakdown")
    if is_upside_gap_three_methods(ohlc_bars):
        _add("upside_gap_three_methods", "BULLISH", 0.58, "bear candle fills gap between two bull candles — gap is support")
    if is_downside_gap_three_methods(ohlc_bars):
        _add("downside_gap_three_methods", "BEARISH", 0.58, "bull candle fills gap between two bear candles — gap is resistance")
    if is_stick_sandwich(ohlc_bars):
        _add("stick_sandwich", "BULLISH", 0.55, "bear-bull-bear with outer bars closing at same price — support holds")
    if is_concealing_baby_swallow(ohlc_bars):
        _add("concealing_baby_swallow", "BULLISH", 0.55, "four bears with middle two having upper wicks — exhaustion")

    return {
        "patterns": results,
        "trend": trend,
        "checked_at": dt.datetime.utcnow().isoformat(),
        "bar_close": c,
        "total_bars": len(ohlc_bars),
    }


def get_patterns_for_ticker(db_url: str, ticker: str, lookback: int = 60) -> Dict[str, Any]:
    """Pull OHLCV from polygon_market_daily and run all 50 candlestick detectors."""
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT open_price, high_price, low_price, close_price,
                       COALESCE(volume, 0) AS volume
                FROM polygon_market_daily
                WHERE ticker = %s AND open_price IS NOT NULL
                ORDER BY scan_date ASC
                LIMIT %s
            """, (ticker, lookback))
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        return {"patterns": [], "error": f"no price data for {ticker}"}
    bars = [{"open": r[0], "high": r[1], "low": r[2], "close": r[3], "volume": r[4]}
            for r in rows]
    result = detect_patterns(bars)
    result["ticker"] = ticker
    return result


if __name__ == "__main__":
    sample_bars = [
        {"open": 100, "high": 102, "low": 98, "close": 99, "volume": 1000000},
        {"open": 99, "high": 105, "low": 97, "close": 104, "volume": 1500000},
    ]
    import json
    print(json.dumps(detect_patterns(sample_bars), indent=2))
