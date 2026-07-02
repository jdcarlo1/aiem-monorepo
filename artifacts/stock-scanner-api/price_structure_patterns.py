"""
price_structure_patterns.py
====================================================================
Pure-algorithm technical analysis, no DB access. Callers (the _mkt_*
wrappers in main.py) fetch OHLC arrays from polygon_market_daily and
pass them in here — keeps this module unit-testable in isolation with
synthetic fixtures.

Covers the parts of the user's indicator/pattern spec NOT already
handled by _mkt_compute_indicators (trend/momentum/volatility/volume):

  - Pivot points: classic, Fibonacci, Camarilla
  - Fibonacci retracement levels from the dominant swing
  - Swing high/low detection (scipy.signal.find_peaks, ATR-scaled prominence)
  - Support/resistance zones (clustered swing prices, scored by touch
    count + recency)
  - Chart patterns (rule-based over the swing sequence): double
    top/bottom, head & shoulders (regular/inverse), triangles
    (ascending/descending/symmetrical), wedges (rising/falling),
    channels (ascending/descending/horizontal), flags/pennants,
    cup and handle

This is deliberately a pragmatic heuristic classifier (linear/quadratic
regression + tolerance bands), not a research-grade ML pattern
recognizer — it is meant to hand AIEM defensible, reproducible flags to
reason from, not final trading signals.

Candlestick patterns live in candlestick_patterns.py (single-bar /
two-bar predicates). This module adds the missing multi-bar ones
(hanging_man, morning_star, evening_star) via wrappers there instead
of duplicating logic.
====================================================================
"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple

try:
    from scipy.signal import find_peaks
except ImportError:  # pragma: no cover - scipy is a project dependency
    find_peaks = None


# ──────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────
def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    n = len(closes)
    if n == 0:
        return 0.0
    tr = np.zeros(n)
    for i in range(n):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    window = tr[-period:] if n >= period else tr
    return float(np.mean(window)) if len(window) else 0.0


def _linreg(xs: np.ndarray, ys: np.ndarray) -> Tuple[float, float, float]:
    """Returns (slope, intercept, r_squared). Degenerate inputs -> zeros."""
    if len(xs) < 2 or len(set(xs.tolist())) < 2:
        return 0.0, float(ys[0]) if len(ys) else 0.0, 0.0
    slope, intercept = np.polyfit(xs, ys, 1)
    pred = slope * xs + intercept
    ss_res = float(np.sum((ys - pred) ** 2))
    ss_tot = float(np.sum((ys - np.mean(ys)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), float(intercept), float(r2)


# ──────────────────────────────────────────────────────────────────────────
# Swing points
# ──────────────────────────────────────────────────────────────────────────
def find_swing_points(highs, lows, closes, atr_val: Optional[float] = None,
                       min_distance: int = 5) -> Tuple[List[Dict], List[Dict], float]:
    """Detect swing highs/lows using ATR-scaled prominence peak detection."""
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    if atr_val is None:
        atr_val = _atr(highs, lows, closes)
    prominence = max(atr_val * 1.5, 1e-6)

    if find_peaks is not None and n >= min_distance * 2:
        peak_idx, _ = find_peaks(highs, distance=min_distance, prominence=prominence)
        trough_idx, _ = find_peaks(-lows, distance=min_distance, prominence=prominence)
        peak_idx = peak_idx.tolist()
        trough_idx = trough_idx.tolist()
    else:
        peak_idx = [i for i in range(min_distance, max(n - min_distance, min_distance))
                    if highs[i] == np.max(highs[max(0, i - min_distance):i + min_distance + 1])]
        trough_idx = [i for i in range(min_distance, max(n - min_distance, min_distance))
                      if lows[i] == np.min(lows[max(0, i - min_distance):i + min_distance + 1])]

    swing_highs = [{"idx": int(i), "price": float(highs[i])} for i in peak_idx]
    swing_lows = [{"idx": int(i), "price": float(lows[i])} for i in trough_idx]
    return swing_highs, swing_lows, atr_val


# ──────────────────────────────────────────────────────────────────────────
# Pivot points
# ──────────────────────────────────────────────────────────────────────────
def compute_pivot_points(prev_high, prev_low, prev_close) -> Optional[Dict[str, Any]]:
    """Classic, Fibonacci, and Camarilla pivots from the prior completed bar."""
    if prev_high is None or prev_low is None or prev_close is None:
        return None
    h, l, c = float(prev_high), float(prev_low), float(prev_close)
    rng = h - l
    if rng <= 0:
        return None
    pivot = (h + l + c) / 3

    classic = {
        "pivot": round(pivot, 4),
        "r1": round(2 * pivot - l, 4), "s1": round(2 * pivot - h, 4),
        "r2": round(pivot + rng, 4), "s2": round(pivot - rng, 4),
        "r3": round(h + 2 * (pivot - l), 4), "s3": round(l - 2 * (h - pivot), 4),
    }
    fibonacci = {
        "pivot": round(pivot, 4),
        "r1": round(pivot + 0.382 * rng, 4), "s1": round(pivot - 0.382 * rng, 4),
        "r2": round(pivot + 0.618 * rng, 4), "s2": round(pivot - 0.618 * rng, 4),
        "r3": round(pivot + 1.000 * rng, 4), "s3": round(pivot - 1.000 * rng, 4),
    }
    camarilla = {
        "r4": round(c + rng * 1.1 / 2, 4), "s4": round(c - rng * 1.1 / 2, 4),
        "r3": round(c + rng * 1.1 / 4, 4), "s3": round(c - rng * 1.1 / 4, 4),
        "r2": round(c + rng * 1.1 / 6, 4), "s2": round(c - rng * 1.1 / 6, 4),
        "r1": round(c + rng * 1.1 / 12, 4), "s1": round(c - rng * 1.1 / 12, 4),
    }
    return {"classic": classic, "fibonacci": fibonacci, "camarilla": camarilla}


def compute_fibonacci_retracement(swing_high, swing_low, uptrend: bool = True) -> Optional[Dict[str, Any]]:
    """Retracement levels for the dominant swing. `uptrend=True` means the
    swing ran low->high and we're measuring pullback levels from the high."""
    if swing_high is None or swing_low is None or swing_high <= swing_low:
        return None
    rng = swing_high - swing_low
    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    levels = {}
    for r in ratios:
        price = swing_high - rng * r if uptrend else swing_low + rng * r
        levels[f"fib_{int(round(r * 1000)):03d}"] = round(price, 4)
    return {
        "swing_high": round(swing_high, 4), "swing_low": round(swing_low, 4),
        "direction": "uptrend" if uptrend else "downtrend", "levels": levels,
    }


# ──────────────────────────────────────────────────────────────────────────
# Support / resistance zones
# ──────────────────────────────────────────────────────────────────────────
def compute_support_resistance_zones(swing_highs: List[Dict], swing_lows: List[Dict],
                                      atr_val: float, current_price: float,
                                      n_bars: int, max_zones: int = 6) -> List[Dict[str, Any]]:
    """Cluster swing prices within an ATR-scaled band; score by touch count + recency."""
    all_points = [(p["idx"], p["price"]) for p in swing_highs] + [(p["idx"], p["price"]) for p in swing_lows]
    if not all_points or current_price is None:
        return []
    band = max(atr_val * 0.5, 1e-6)
    all_points.sort(key=lambda x: x[1])

    clusters: List[Dict[str, Any]] = []
    for idx, price in all_points:
        placed = False
        for cl in clusters:
            if abs(price - cl["avg_price"]) <= band:
                cl["prices"].append(price)
                cl["idxs"].append(idx)
                cl["avg_price"] = float(np.mean(cl["prices"]))
                placed = True
                break
        if not placed:
            clusters.append({"prices": [price], "idxs": [idx], "avg_price": price})

    zones = []
    for cl in clusters:
        touch_count = len(cl["prices"])
        recency = max(cl["idxs"]) / max(n_bars - 1, 1)
        score = touch_count * (0.5 + 0.5 * recency)
        zone_type = "resistance" if cl["avg_price"] > current_price else "support"
        zones.append({
            "level": round(cl["avg_price"], 4),
            "type": zone_type,
            "touch_count": touch_count,
            "recency_score": round(recency, 3),
            "significance_score": round(score, 3),
            "distance_pct": round((current_price - cl["avg_price"]) / cl["avg_price"] * 100, 2) if cl["avg_price"] else None,
        })
    zones.sort(key=lambda z: z["significance_score"], reverse=True)
    # only surface statistically meaningful zones: multi-touch, or a single
    # very recent + high-score touch. Falls back to top-3 raw if nothing clears the bar.
    significant = [z for z in zones if z["touch_count"] >= 2 or z["significance_score"] >= 1.0]
    return (significant or zones)[:max_zones]


# ──────────────────────────────────────────────────────────────────────────
# Chart pattern classification
# ──────────────────────────────────────────────────────────────────────────
def classify_chart_patterns(swing_highs: List[Dict], swing_lows: List[Dict],
                             closes, highs, lows, atr_val: float,
                             tolerance_pct: float = 3.0) -> List[Dict[str, Any]]:
    """Rule-based chart pattern detection over the swing-point sequence.
    Returns a list of flagged patterns only (empty list if nothing clears
    the confidence bar) — never a "no pattern" negative result."""
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    n = len(closes)
    if n < 10:
        return []

    current_price = float(closes[-1])
    avg_price = float(np.mean(closes[-min(n, 60):])) or 1.0
    patterns: List[Dict[str, Any]] = []

    def pct_diff(a, b):
        return abs(a - b) / ((a + b) / 2) * 100 if (a + b) else 0.0

    # ── Double top / double bottom (last 2 peaks / last 2 troughs) ────────
    if len(swing_highs) >= 2:
        p1, p2 = swing_highs[-2], swing_highs[-1]
        if pct_diff(p1["price"], p2["price"]) <= tolerance_pct:
            between = [t for t in swing_lows if p1["idx"] < t["idx"] < p2["idx"]]
            if between:
                trough = min(between, key=lambda t: t["price"])
                depth_pct = pct_diff(trough["price"], (p1["price"] + p2["price"]) / 2)
                if depth_pct >= tolerance_pct:
                    confirmed = current_price < trough["price"]
                    patterns.append({
                        "pattern": "double_top", "direction": "bearish",
                        "status": "confirmed" if confirmed else "forming",
                        "confidence": round(min(0.55 + depth_pct / 100, 0.85), 2),
                        "key_levels": {"peak_avg": round((p1["price"] + p2["price"]) / 2, 4),
                                       "neckline": round(trough["price"], 4)},
                        "reason": f"two peaks within {tolerance_pct}% of each other with a "
                                  f"{depth_pct:.1f}% intervening pullback",
                    })

    if len(swing_lows) >= 2:
        t1, t2 = swing_lows[-2], swing_lows[-1]
        if pct_diff(t1["price"], t2["price"]) <= tolerance_pct:
            between = [p for p in swing_highs if t1["idx"] < p["idx"] < t2["idx"]]
            if between:
                peak = max(between, key=lambda p: p["price"])
                height_pct = pct_diff(peak["price"], (t1["price"] + t2["price"]) / 2)
                if height_pct >= tolerance_pct:
                    confirmed = current_price > peak["price"]
                    patterns.append({
                        "pattern": "double_bottom", "direction": "bullish",
                        "status": "confirmed" if confirmed else "forming",
                        "confidence": round(min(0.55 + height_pct / 100, 0.85), 2),
                        "key_levels": {"trough_avg": round((t1["price"] + t2["price"]) / 2, 4),
                                       "neckline": round(peak["price"], 4)},
                        "reason": f"two troughs within {tolerance_pct}% of each other with a "
                                  f"{height_pct:.1f}% intervening bounce",
                    })

    # ── Head & shoulders (regular / inverse), last 3 peaks + 2 troughs ────
    if len(swing_highs) >= 3 and len(swing_lows) >= 2:
        p1, p2, p3 = swing_highs[-3], swing_highs[-2], swing_highs[-1]
        if p2["price"] > p1["price"] and p2["price"] > p3["price"] and pct_diff(p1["price"], p3["price"]) <= tolerance_pct * 1.5:
            neck = [t for t in swing_lows if p1["idx"] < t["idx"] < p3["idx"]]
            if len(neck) >= 2:
                neck_sorted = sorted(neck, key=lambda t: t["idx"])[:2]
                slope, intercept, _ = _linreg(np.array([t["idx"] for t in neck_sorted]),
                                               np.array([t["price"] for t in neck_sorted]))
                neckline_now = slope * (n - 1) + intercept
                confirmed = current_price < neckline_now
                patterns.append({
                    "pattern": "head_and_shoulders", "direction": "bearish",
                    "status": "confirmed" if confirmed else "forming",
                    "confidence": 0.7 if confirmed else 0.55,
                    "key_levels": {"head": round(p2["price"], 4), "neckline_now": round(neckline_now, 4)},
                    "reason": "middle peak highest, shoulders symmetric within tolerance",
                })
    if len(swing_lows) >= 3 and len(swing_highs) >= 2:
        t1, t2, t3 = swing_lows[-3], swing_lows[-2], swing_lows[-1]
        if t2["price"] < t1["price"] and t2["price"] < t3["price"] and pct_diff(t1["price"], t3["price"]) <= tolerance_pct * 1.5:
            neck = [p for p in swing_highs if t1["idx"] < p["idx"] < t3["idx"]]
            if len(neck) >= 2:
                neck_sorted = sorted(neck, key=lambda p: p["idx"])[:2]
                slope, intercept, _ = _linreg(np.array([p["idx"] for p in neck_sorted]),
                                               np.array([p["price"] for p in neck_sorted]))
                neckline_now = slope * (n - 1) + intercept
                confirmed = current_price > neckline_now
                patterns.append({
                    "pattern": "inverse_head_and_shoulders", "direction": "bullish",
                    "status": "confirmed" if confirmed else "forming",
                    "confidence": 0.7 if confirmed else 0.55,
                    "key_levels": {"head": round(t2["price"], 4), "neckline_now": round(neckline_now, 4)},
                    "reason": "middle trough lowest, shoulders symmetric within tolerance",
                })

    # ── Triangles / wedges / channels: slope of recent peaks vs troughs ───
    recent_peaks = swing_highs[-4:]
    recent_troughs = swing_lows[-4:]
    if len(recent_peaks) >= 2 and len(recent_troughs) >= 2:
        p_slope, _, p_r2 = _linreg(np.array([p["idx"] for p in recent_peaks]), np.array([p["price"] for p in recent_peaks]))
        t_slope, _, t_r2 = _linreg(np.array([t["idx"] for t in recent_troughs]), np.array([t["price"] for t in recent_troughs]))
        flat_thresh = max(atr_val * 0.05, avg_price * 0.0005)
        p_flat, t_flat = abs(p_slope) < flat_thresh, abs(t_slope) < flat_thresh
        p_up, p_down = p_slope > flat_thresh, p_slope < -flat_thresh
        t_up, t_down = t_slope > flat_thresh, t_slope < -flat_thresh
        fit_ok = (p_r2 >= 0.3 or p_flat) and (t_r2 >= 0.3 or t_flat)
        eff_p_r2 = 1.0 if p_flat else p_r2
        eff_t_r2 = 1.0 if t_flat else t_r2
        conf_base = round(0.45 + 0.15 * min(eff_p_r2, eff_t_r2), 2)

        if fit_ok:
            if p_flat and t_up:
                patterns.append({"pattern": "triangle_ascending", "direction": "bullish", "status": "forming",
                                  "confidence": conf_base, "key_levels": {"resistance": round(recent_peaks[-1]["price"], 4)},
                                  "reason": "flat top, rising bottom — converging"})
            elif t_flat and p_down:
                patterns.append({"pattern": "triangle_descending", "direction": "bearish", "status": "forming",
                                  "confidence": conf_base, "key_levels": {"support": round(recent_troughs[-1]["price"], 4)},
                                  "reason": "flat bottom, falling top — converging"})
            elif p_down and t_up:
                patterns.append({"pattern": "triangle_symmetrical", "direction": "neutral", "status": "forming",
                                  "confidence": conf_base, "key_levels": {},
                                  "reason": "top falling and bottom rising — converging from both sides"})
            elif p_up and t_up and p_slope < t_slope:
                patterns.append({"pattern": "wedge_rising", "direction": "bearish", "status": "forming",
                                  "confidence": conf_base, "key_levels": {},
                                  "reason": "both trendlines rising but converging — weakening upside momentum"})
            elif p_down and t_down and p_slope > t_slope:
                patterns.append({"pattern": "wedge_falling", "direction": "bullish", "status": "forming",
                                  "confidence": conf_base, "key_levels": {},
                                  "reason": "both trendlines falling but converging — weakening downside momentum"})
            elif p_up and t_up and abs(p_slope - t_slope) < flat_thresh * 2:
                patterns.append({"pattern": "channel_ascending", "direction": "bullish", "status": "forming",
                                  "confidence": conf_base, "key_levels": {}, "reason": "parallel rising trendlines"})
            elif p_down and t_down and abs(p_slope - t_slope) < flat_thresh * 2:
                patterns.append({"pattern": "channel_descending", "direction": "bearish", "status": "forming",
                                  "confidence": conf_base, "key_levels": {}, "reason": "parallel falling trendlines"})
            elif p_flat and t_flat:
                patterns.append({"pattern": "channel_horizontal", "direction": "neutral", "status": "forming",
                                  "confidence": conf_base, "key_levels": {}, "reason": "flat top and bottom — range-bound"})

    # ── Flags / pennants: strong pole then tight, short consolidation ─────
    pole_window = min(n, 20)
    if n >= pole_window + 10:
        pole = closes[-(pole_window + 10):-10]
        pole_move = pole[-1] - pole[0]
        if abs(pole_move) >= 3 * atr_val:
            consolidation = closes[-10:]
            cons_range = float(np.max(consolidation) - np.min(consolidation))
            if cons_range <= 1.5 * atr_val:
                direction = "bullish" if pole_move > 0 else "bearish"
                patterns.append({
                    "pattern": "flag_or_pennant", "direction": direction, "status": "forming",
                    "confidence": 0.5,
                    "key_levels": {"consolidation_high": round(float(np.max(consolidation)), 4),
                                   "consolidation_low": round(float(np.min(consolidation)), 4)},
                    "reason": f"{'up' if pole_move > 0 else 'down'} pole of {abs(pole_move):.2f} "
                              f"followed by a tight {cons_range:.2f}-range consolidation",
                })

    # ── Cup and handle (lowest confidence — conservative thresholds) ──────
    cup_window = min(n, 40)
    if n >= cup_window:
        seg = closes[-cup_window:]
        xs = np.arange(len(seg), dtype=float)
        a, b, c = np.polyfit(xs, seg, 2)
        pred = a * xs ** 2 + b * xs + c
        ss_res = float(np.sum((seg - pred) ** 2))
        ss_tot = float(np.sum((seg - np.mean(seg)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rim_left, rim_right = seg[0], seg[-1]
        bottom = float(np.min(seg))
        depth_pct = pct_diff(bottom, (rim_left + rim_right) / 2)
        if a > 0 and r2 >= 0.55 and depth_pct >= 5 and pct_diff(rim_left, rim_right) <= tolerance_pct * 2:
            handle = seg[-8:]
            handle_range_pct = (float(np.max(handle) - np.min(handle)) / avg_price) * 100
            if handle_range_pct <= depth_pct * 0.5:
                patterns.append({
                    "pattern": "cup_and_handle", "direction": "bullish", "status": "forming",
                    "confidence": round(min(0.4 + r2 * 0.2, 0.6), 2),
                    "key_levels": {"rim": round((rim_left + rim_right) / 2, 4), "bottom": round(bottom, 4)},
                    "reason": f"rounded bottom fit R²={r2:.2f}, {depth_pct:.1f}% deep, shallow handle pullback",
                })

    return patterns
