"""
price_structure_patterns.py
====================================================================
Pure-algorithm technical analysis, no DB access. Callers fetch OHLCV
arrays from polygon_market_daily and pass them in here.

Patterns covered (30 total):
  Existing: double top/bottom, H&S/inverse H&S, triangles (3),
            wedges (2), channels (3), flags/pennants, cup & handle

  Added:    triple top/bottom, diamond top/bottom, complex H&S,
            broadening/megaphone (3), rounded top/bottom (saucer),
            inverted cup & handle, gaps (4 types),
            island reversal (bullish/bearish), measured move (up/down)

All pattern results are dicts with: pattern, category, direction,
status, confidence, key_levels, reason.
====================================================================
"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple

try:
    from scipy.signal import find_peaks
except ImportError:
    find_peaks = None


# ── Shared helpers ────────────────────────────────────────────────────────────

def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    n = len(closes)
    if n == 0:
        return 0.0
    tr = np.zeros(n)
    for i in range(n):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]))
    window = tr[-period:] if n >= period else tr
    return float(np.mean(window)) if len(window) else 0.0


def _linreg(xs: np.ndarray, ys: np.ndarray) -> Tuple[float, float, float]:
    if len(xs) < 2 or len(set(xs.tolist())) < 2:
        return 0.0, float(ys[0]) if len(ys) else 0.0, 0.0
    slope, intercept = np.polyfit(xs, ys, 1)
    pred = slope * xs + intercept
    ss_res = float(np.sum((ys - pred) ** 2))
    ss_tot = float(np.sum((ys - np.mean(ys)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), float(intercept), float(r2)


def pct_diff(a, b):
    return abs(a - b) / ((a + b) / 2) * 100 if (a + b) else 0.0


# ── Swing points ──────────────────────────────────────────────────────────────

def find_swing_points(highs, lows, closes, atr_val: Optional[float] = None,
                      min_distance: int = 5) -> Tuple[List[Dict], List[Dict], float]:
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


# ── Pivot points ──────────────────────────────────────────────────────────────

def compute_pivot_points(prev_high, prev_low, prev_close) -> Optional[Dict[str, Any]]:
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
        "r2": round(pivot + rng, 4),   "s2": round(pivot - rng, 4),
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


# ── Support / resistance zones ────────────────────────────────────────────────

def compute_support_resistance_zones(swing_highs: List[Dict], swing_lows: List[Dict],
                                      atr_val: float, current_price: float,
                                      n_bars: int, max_zones: int = 6) -> List[Dict[str, Any]]:
    all_points = [(p["idx"], p["price"]) for p in swing_highs] + \
                 [(p["idx"], p["price"]) for p in swing_lows]
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
            "level": round(cl["avg_price"], 4), "type": zone_type,
            "touch_count": touch_count, "recency_score": round(recency, 3),
            "significance_score": round(score, 3),
            "distance_pct": round((current_price - cl["avg_price"]) / cl["avg_price"] * 100, 2) if cl["avg_price"] else None,
        })
    zones.sort(key=lambda z: z["significance_score"], reverse=True)
    significant = [z for z in zones if z["touch_count"] >= 2 or z["significance_score"] >= 1.0]
    return (significant or zones)[:max_zones]


# ── Chart pattern classification ──────────────────────────────────────────────

def classify_chart_patterns(swing_highs: List[Dict], swing_lows: List[Dict],
                             closes, highs, lows, atr_val: float,
                             tolerance_pct: float = 3.0) -> List[Dict[str, Any]]:
    """
    Rule-based chart pattern detection over the swing-point sequence.
    Returns a list of flagged patterns only (empty list if nothing clears
    the confidence bar) — never a "no pattern" negative result.
    """
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    n = len(closes)
    if n < 10:
        return []

    current_price = float(closes[-1])
    avg_price = float(np.mean(closes[-min(n, 60):])) or 1.0
    patterns: List[Dict[str, Any]] = []

    # ── Double top / double bottom ────────────────────────────────────────────
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
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"peak_avg": round((p1["price"] + p2["price"]) / 2, 4),
                                       "neckline": round(trough["price"], 4)},
                        "reason": f"two peaks within {tolerance_pct}% with {depth_pct:.1f}% pullback",
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
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"trough_avg": round((t1["price"] + t2["price"]) / 2, 4),
                                       "neckline": round(peak["price"], 4)},
                        "reason": f"two troughs within {tolerance_pct}% with {height_pct:.1f}% bounce",
                    })

    # ── Triple top / triple bottom ────────────────────────────────────────────
    if len(swing_highs) >= 3:
        p1, p2, p3 = swing_highs[-3], swing_highs[-2], swing_highs[-1]
        if (pct_diff(p1["price"], p2["price"]) <= tolerance_pct
                and pct_diff(p2["price"], p3["price"]) <= tolerance_pct):
            between_lows = [t for t in swing_lows if p1["idx"] < t["idx"] < p3["idx"]]
            if len(between_lows) >= 2:
                neckline = max(between_lows, key=lambda t: t["price"])["price"]
                confirmed = current_price < neckline
                patterns.append({
                    "pattern": "triple_top", "direction": "bearish",
                    "status": "confirmed" if confirmed else "forming",
                    "confidence": 0.72 if confirmed else 0.58,
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"peak_avg": round((p1["price"] + p2["price"] + p3["price"]) / 3, 4),
                                   "neckline": round(neckline, 4)},
                    "reason": "three peaks within tolerance — strong overhead resistance",
                })
    if len(swing_lows) >= 3:
        t1, t2, t3 = swing_lows[-3], swing_lows[-2], swing_lows[-1]
        if (pct_diff(t1["price"], t2["price"]) <= tolerance_pct
                and pct_diff(t2["price"], t3["price"]) <= tolerance_pct):
            between_highs = [p for p in swing_highs if t1["idx"] < p["idx"] < t3["idx"]]
            if len(between_highs) >= 2:
                neckline = min(between_highs, key=lambda p: p["price"])["price"]
                confirmed = current_price > neckline
                patterns.append({
                    "pattern": "triple_bottom", "direction": "bullish",
                    "status": "confirmed" if confirmed else "forming",
                    "confidence": 0.72 if confirmed else 0.58,
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"trough_avg": round((t1["price"] + t2["price"] + t3["price"]) / 3, 4),
                                   "neckline": round(neckline, 4)},
                    "reason": "three troughs within tolerance — strong support confirmed",
                })

    # ── Head & shoulders (regular / inverse) ──────────────────────────────────
    if len(swing_highs) >= 3 and len(swing_lows) >= 2:
        p1, p2, p3 = swing_highs[-3], swing_highs[-2], swing_highs[-1]
        if (p2["price"] > p1["price"] and p2["price"] > p3["price"]
                and pct_diff(p1["price"], p3["price"]) <= tolerance_pct * 1.5):
            neck = [t for t in swing_lows if p1["idx"] < t["idx"] < p3["idx"]]
            if len(neck) >= 2:
                neck_sorted = sorted(neck, key=lambda t: t["idx"])[:2]
                slope, intercept, _ = _linreg(
                    np.array([t["idx"] for t in neck_sorted]),
                    np.array([t["price"] for t in neck_sorted]))
                neckline_now = slope * (n - 1) + intercept
                confirmed = current_price < neckline_now
                patterns.append({
                    "pattern": "head_and_shoulders", "direction": "bearish",
                    "status": "confirmed" if confirmed else "forming",
                    "confidence": 0.70 if confirmed else 0.55,
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"head": round(p2["price"], 4),
                                   "neckline_now": round(neckline_now, 4)},
                    "reason": "middle peak highest, shoulders symmetric within tolerance",
                })
    if len(swing_lows) >= 3 and len(swing_highs) >= 2:
        t1, t2, t3 = swing_lows[-3], swing_lows[-2], swing_lows[-1]
        if (t2["price"] < t1["price"] and t2["price"] < t3["price"]
                and pct_diff(t1["price"], t3["price"]) <= tolerance_pct * 1.5):
            neck = [p for p in swing_highs if t1["idx"] < p["idx"] < t3["idx"]]
            if len(neck) >= 2:
                neck_sorted = sorted(neck, key=lambda p: p["idx"])[:2]
                slope, intercept, _ = _linreg(
                    np.array([p["idx"] for p in neck_sorted]),
                    np.array([p["price"] for p in neck_sorted]))
                neckline_now = slope * (n - 1) + intercept
                confirmed = current_price > neckline_now
                patterns.append({
                    "pattern": "inverse_head_and_shoulders", "direction": "bullish",
                    "status": "confirmed" if confirmed else "forming",
                    "confidence": 0.70 if confirmed else 0.55,
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"head": round(t2["price"], 4),
                                   "neckline_now": round(neckline_now, 4)},
                    "reason": "middle trough lowest, shoulders symmetric within tolerance",
                })

    # ── Complex H&S (≥4 swing highs: 2 left shoulders, head, right shoulder) ─
    if len(swing_highs) >= 4 and len(swing_lows) >= 3:
        p1, p2, p3, p4 = swing_highs[-4], swing_highs[-3], swing_highs[-2], swing_highs[-1]
        head = max(p1["price"], p2["price"], p3["price"], p4["price"])
        if head == p2["price"] or head == p3["price"]:
            shoulder_avg = (p1["price"] + p4["price"]) / 2
            if pct_diff(shoulder_avg, head) >= 3 and pct_diff(p1["price"], p4["price"]) <= tolerance_pct * 2:
                neck = [t for t in swing_lows if p1["idx"] < t["idx"] < p4["idx"]]
                if len(neck) >= 2:
                    patterns.append({
                        "pattern": "complex_head_and_shoulders", "direction": "bearish",
                        "status": "forming",
                        "confidence": 0.62,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"head_price": round(head, 4),
                                       "outer_shoulders": round(shoulder_avg, 4)},
                        "reason": "4-peak complex H&S — multiple shoulders increase reliability",
                    })

    # ── Diamond top / Diamond bottom ──────────────────────────────────────────
    if len(swing_highs) >= 4 and len(swing_lows) >= 3 and n >= 20:
        first_half_peaks = swing_highs[-4:-2]
        first_half_troughs = swing_lows[-3:-1]
        last_half_peaks = swing_highs[-2:]
        last_half_troughs = swing_lows[-1:]
        if first_half_peaks and first_half_troughs and last_half_peaks and last_half_troughs:
            p_range_first = max(p["price"] for p in first_half_peaks) - min(t["price"] for t in first_half_troughs)
            p_range_last = max(p["price"] for p in last_half_peaks) - min(t["price"] for t in last_half_troughs)
            avg_range_arr = float(np.mean(highs[-20:] - lows[-20:]))
            if avg_range_arr > 0 and p_range_first > avg_range_arr * 2 and p_range_last < p_range_first * 0.75:
                slope_h = _linreg(np.array([p["idx"] for p in swing_highs[-4:]]),
                                   np.array([p["price"] for p in swing_highs[-4:]]))[0]
                slope_l = _linreg(np.array([t["idx"] for t in swing_lows[-3:]]),
                                   np.array([t["price"] for t in swing_lows[-3:]]))[0]
                if slope_h < 0 and slope_l > 0:
                    patterns.append({
                        "pattern": "diamond_top", "direction": "bearish",
                        "status": "forming",
                        "confidence": 0.58,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {},
                        "reason": "broadening then contracting range at top — Diamond Top",
                    })
    if len(swing_lows) >= 4 and n >= 20:
        p_range_first = max(h["price"] for h in swing_highs[-3:-1]) - min(t["price"] for t in swing_lows[-4:-2]) if len(swing_highs) >= 3 else 0
        p_range_last = max(h["price"] for h in swing_highs[-1:]) - min(t["price"] for t in swing_lows[-2:]) if swing_highs else 0
        avg_rng = float(np.mean(highs[-20:] - lows[-20:]))
        if avg_rng > 0 and p_range_first > avg_rng * 2 and p_range_last < p_range_first * 0.75:
            patterns.append({
                "pattern": "diamond_bottom", "direction": "bullish",
                "status": "forming",
                "confidence": 0.58,
                "category": "CHART_STRUCTURE",
                "key_levels": {},
                "reason": "broadening then contracting range at bottom — Diamond Bottom",
            })

    # ── Triangles / wedges / channels ─────────────────────────────────────────
    recent_peaks = swing_highs[-4:]
    recent_troughs = swing_lows[-4:]
    if len(recent_peaks) >= 2 and len(recent_troughs) >= 2:
        p_slope, _, p_r2 = _linreg(np.array([p["idx"] for p in recent_peaks]),
                                    np.array([p["price"] for p in recent_peaks]))
        t_slope, _, t_r2 = _linreg(np.array([t["idx"] for t in recent_troughs]),
                                    np.array([t["price"] for t in recent_troughs]))
        flat_thresh = max(atr_val * 0.05, avg_price * 0.0005)
        p_flat = abs(p_slope) < flat_thresh
        t_flat = abs(t_slope) < flat_thresh
        p_up = p_slope > flat_thresh
        p_down = p_slope < -flat_thresh
        t_up = t_slope > flat_thresh
        t_down = t_slope < -flat_thresh
        fit_ok = (p_r2 >= 0.3 or p_flat) and (t_r2 >= 0.3 or t_flat)
        conf_base = round(0.45 + 0.15 * min(
            1.0 if p_flat else p_r2,
            1.0 if t_flat else t_r2), 2)

        if fit_ok:
            if p_flat and t_up:
                patterns.append({"pattern": "triangle_ascending", "direction": "bullish",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {"resistance": round(recent_peaks[-1]["price"], 4)},
                                  "reason": "flat top, rising bottom — converging"})
            elif t_flat and p_down:
                patterns.append({"pattern": "triangle_descending", "direction": "bearish",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {"support": round(recent_troughs[-1]["price"], 4)},
                                  "reason": "flat bottom, falling top — converging"})
            elif p_down and t_up:
                patterns.append({"pattern": "triangle_symmetrical", "direction": "neutral",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {},
                                  "reason": "top falling and bottom rising — converging from both sides"})
            elif p_up and t_up and p_slope < t_slope:
                patterns.append({"pattern": "wedge_rising", "direction": "bearish",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {},
                                  "reason": "both trendlines rising but converging — weakening upside momentum"})
            elif p_down and t_down and p_slope > t_slope:
                patterns.append({"pattern": "wedge_falling", "direction": "bullish",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {},
                                  "reason": "both trendlines falling but converging — weakening downside momentum"})
            elif p_up and t_up and abs(p_slope - t_slope) < flat_thresh * 2:
                patterns.append({"pattern": "channel_ascending", "direction": "bullish",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {}, "reason": "parallel rising trendlines"})
            elif p_down and t_down and abs(p_slope - t_slope) < flat_thresh * 2:
                patterns.append({"pattern": "channel_descending", "direction": "bearish",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {}, "reason": "parallel falling trendlines"})
            elif p_flat and t_flat:
                patterns.append({"pattern": "channel_horizontal", "direction": "neutral",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {}, "reason": "flat top and bottom — range-bound"})
            # ── Broadening / Megaphone ─────────────────────────────────────
            elif p_up and t_down:
                patterns.append({"pattern": "broadening_symmetrical", "direction": "neutral",
                                  "status": "forming", "confidence": conf_base,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {},
                                  "reason": "top rising and bottom falling — expanding range, high uncertainty"})
            elif p_up and t_flat:
                patterns.append({"pattern": "broadening_top", "direction": "bearish",
                                  "status": "forming", "confidence": conf_base * 0.9,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {},
                                  "reason": "flat support, rising resistance — broadening top (bearish bias)"})
            elif p_flat and t_down:
                patterns.append({"pattern": "broadening_bottom", "direction": "bullish",
                                  "status": "forming", "confidence": conf_base * 0.9,
                                  "category": "CHART_STRUCTURE",
                                  "key_levels": {},
                                  "reason": "flat resistance, falling support — broadening bottom (bullish bias)"})

    # ── Flags / pennants ──────────────────────────────────────────────────────
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
                    "pattern": "flag_or_pennant", "direction": direction,
                    "status": "forming", "confidence": 0.52,
                    "category": "CHART_STRUCTURE",
                    "key_levels": {
                        "consolidation_high": round(float(np.max(consolidation)), 4),
                        "consolidation_low":  round(float(np.min(consolidation)), 4),
                    },
                    "reason": (f"{'up' if pole_move > 0 else 'down'} pole of {abs(pole_move):.2f} "
                               f"then tight {cons_range:.2f}-range consolidation"),
                })

    # ── Cup and handle ────────────────────────────────────────────────────────
    cup_window = min(n, 40)
    if n >= cup_window:
        seg = closes[-cup_window:]
        xs = np.arange(len(seg), dtype=float)
        a_coef, b_coef, c_coef = np.polyfit(xs, seg, 2)
        pred = a_coef * xs ** 2 + b_coef * xs + c_coef
        ss_res = float(np.sum((seg - pred) ** 2))
        ss_tot = float(np.sum((seg - np.mean(seg)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rim_left, rim_right = seg[0], seg[-1]
        bottom = float(np.min(seg))
        depth_pct = pct_diff(bottom, (rim_left + rim_right) / 2)
        if a_coef > 0 and r2 >= 0.55 and depth_pct >= 5 and pct_diff(rim_left, rim_right) <= tolerance_pct * 2:
            handle = seg[-8:]
            handle_range_pct = (float(np.max(handle) - np.min(handle)) / avg_price) * 100
            if handle_range_pct <= depth_pct * 0.5:
                patterns.append({
                    "pattern": "cup_and_handle", "direction": "bullish",
                    "status": "forming",
                    "confidence": round(min(0.4 + r2 * 0.2, 0.6), 2),
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"rim": round((rim_left + rim_right) / 2, 4),
                                   "bottom": round(bottom, 4)},
                    "reason": f"rounded bottom R²={r2:.2f}, {depth_pct:.1f}% deep, shallow handle",
                })

    # ── Inverted cup & handle (bearish reversal) ──────────────────────────────
    if n >= cup_window:
        seg = closes[-cup_window:]
        xs = np.arange(len(seg), dtype=float)
        a_coef, _, _ = np.polyfit(xs, seg, 2)
        pred = a_coef * xs ** 2 + (-2 * a_coef * len(seg) / 2) * xs + 0
        ss_res = float(np.sum((seg - (a_coef * xs ** 2 + np.polyfit(xs, seg, 2)[1] * xs + np.polyfit(xs, seg, 2)[2])) ** 2))
        coeffs = np.polyfit(xs, seg, 2)
        pred = coeffs[0] * xs ** 2 + coeffs[1] * xs + coeffs[2]
        ss_res = float(np.sum((seg - pred) ** 2))
        ss_tot = float(np.sum((seg - np.mean(seg)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rim_left, rim_right = seg[0], seg[-1]
        top = float(np.max(seg))
        height_pct = pct_diff(top, (rim_left + rim_right) / 2)
        if (coeffs[0] < 0 and r2 >= 0.50 and height_pct >= 5
                and pct_diff(rim_left, rim_right) <= tolerance_pct * 2):
            handle = seg[-8:]
            handle_range_pct = (float(np.max(handle) - np.min(handle)) / avg_price) * 100
            if handle_range_pct <= height_pct * 0.5:
                patterns.append({
                    "pattern": "inverted_cup_and_handle", "direction": "bearish",
                    "status": "forming",
                    "confidence": round(min(0.4 + r2 * 0.2, 0.58), 2),
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"rim": round((rim_left + rim_right) / 2, 4),
                                   "top": round(top, 4)},
                    "reason": f"rounded top R²={r2:.2f}, {height_pct:.1f}% high, shallow handle",
                })

    # ── Rounded top / Rounded bottom (Saucer) ────────────────────────────────
    saucer_window = min(n, 30)
    if n >= saucer_window:
        seg = closes[-saucer_window:]
        xs = np.arange(len(seg), dtype=float)
        coeffs = np.polyfit(xs, seg, 2)
        pred = coeffs[0] * xs ** 2 + coeffs[1] * xs + coeffs[2]
        ss_res = float(np.sum((seg - pred) ** 2))
        ss_tot = float(np.sum((seg - np.mean(seg)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        if r2 >= 0.65 and abs(seg[-1] - seg[0]) <= atr_val * 2:
            if coeffs[0] > 0:
                patterns.append({
                    "pattern": "rounded_bottom", "direction": "bullish",
                    "status": "forming", "confidence": round(min(0.45 + r2 * 0.15, 0.60), 2),
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"bottom": round(float(np.min(seg)), 4),
                                   "rim_left": round(float(seg[0]), 4),
                                   "rim_right": round(float(seg[-1]), 4)},
                    "reason": f"parabolic R²={r2:.2f} concave-up bowl — Saucer/Rounded Bottom",
                })
            elif coeffs[0] < 0:
                patterns.append({
                    "pattern": "rounded_top", "direction": "bearish",
                    "status": "forming", "confidence": round(min(0.45 + r2 * 0.15, 0.60), 2),
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"top": round(float(np.max(seg)), 4)},
                    "reason": f"parabolic R²={r2:.2f} concave-down arc — Rounded Top",
                })

    # ── Gap patterns ──────────────────────────────────────────────────────────
    # Requires OHLC bars, approximated from closes + highs/lows arrays
    if n >= 5:
        for i in range(max(n - 5, 1), n):
            if i == 0:
                continue
            gap_up_size = closes[i] - highs[i - 1] if closes[i] > highs[i - 1] else 0
            gap_dn_size = lows[i - 1] - closes[i] if closes[i] < lows[i - 1] else 0

            # Breakaway Gap: large gap at start of new trend on rising volume
            if gap_up_size > atr_val * 0.8 and i == n - 1:
                trend_pct = (closes[i] - closes[max(0, i - 10)]) / closes[max(0, i - 10)] * 100
                if trend_pct < 0:
                    patterns.append({
                        "pattern": "breakaway_gap_up", "direction": "bullish",
                        "status": "confirmed", "confidence": 0.62,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"gap_bottom": round(float(highs[i - 1]), 4),
                                       "gap_top": round(float(closes[i]), 4),
                                       "gap_size": round(gap_up_size, 4)},
                        "reason": f"large upside gap ({gap_up_size:.2f}) after downtrend — Breakaway Gap",
                    })
            if gap_dn_size > atr_val * 0.8 and i == n - 1:
                trend_pct = (closes[i] - closes[max(0, i - 10)]) / closes[max(0, i - 10)] * 100
                if trend_pct > 0:
                    patterns.append({
                        "pattern": "breakaway_gap_down", "direction": "bearish",
                        "status": "confirmed", "confidence": 0.62,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"gap_top": round(float(lows[i - 1]), 4),
                                       "gap_bottom": round(float(closes[i]), 4),
                                       "gap_size": round(gap_dn_size, 4)},
                        "reason": f"large downside gap ({gap_dn_size:.2f}) after uptrend — Breakaway Gap",
                    })

            # Runaway/Measuring Gap: gap in middle of existing trend
            if gap_up_size > atr_val * 0.4 and i == n - 1:
                trend_pct = (closes[i] - closes[max(0, i - 10)]) / closes[max(0, i - 10)] * 100
                if trend_pct > 3:
                    patterns.append({
                        "pattern": "runaway_gap_up", "direction": "bullish",
                        "status": "confirmed", "confidence": 0.58,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"gap_size": round(gap_up_size, 4)},
                        "reason": "upside gap within existing uptrend — Runaway/Measuring Gap",
                    })
            if gap_dn_size > atr_val * 0.4 and i == n - 1:
                trend_pct = (closes[i] - closes[max(0, i - 10)]) / closes[max(0, i - 10)] * 100
                if trend_pct < -3:
                    patterns.append({
                        "pattern": "runaway_gap_down", "direction": "bearish",
                        "status": "confirmed", "confidence": 0.58,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"gap_size": round(gap_dn_size, 4)},
                        "reason": "downside gap within existing downtrend — Runaway/Measuring Gap",
                    })

            # Exhaustion Gap: gap at end of move, small, quickly fills
            if gap_up_size > 0 and i == n - 1 and gap_up_size < atr_val * 0.5:
                trend_pct = (closes[i] - closes[max(0, i - 15)]) / closes[max(0, i - 15)] * 100
                if trend_pct > 10:
                    patterns.append({
                        "pattern": "exhaustion_gap_up", "direction": "bearish",
                        "status": "forming", "confidence": 0.50,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"gap_size": round(gap_up_size, 4)},
                        "reason": "small upside gap after extended uptrend — potential Exhaustion Gap",
                    })
            if gap_dn_size > 0 and i == n - 1 and gap_dn_size < atr_val * 0.5:
                trend_pct = (closes[i] - closes[max(0, i - 15)]) / closes[max(0, i - 15)] * 100
                if trend_pct < -10:
                    patterns.append({
                        "pattern": "exhaustion_gap_down", "direction": "bullish",
                        "status": "forming", "confidence": 0.50,
                        "category": "CHART_STRUCTURE",
                        "key_levels": {"gap_size": round(gap_dn_size, 4)},
                        "reason": "small downside gap after extended downtrend — potential Exhaustion Gap",
                    })

    # ── Island reversal ───────────────────────────────────────────────────────
    if n >= 5:
        for i in range(2, n):
            # Bullish island: gap down to isolate lows, then gap up
            gap1_dn = lows[i - 2] > highs[i - 1] if lows[i - 2] > 0 and highs[i - 1] > 0 else False
            gap2_up = lows[i] > highs[i - 1] if i < n else False
            if gap1_dn and gap2_up and i == n - 1:
                patterns.append({
                    "pattern": "island_reversal_bullish", "direction": "bullish",
                    "status": "confirmed", "confidence": 0.70,
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"island_high": round(float(highs[i - 1]), 4),
                                   "island_low": round(float(lows[i - 1]), 4)},
                    "reason": "isolated candle with gap-down entry and gap-up exit — Bullish Island Reversal",
                })
            # Bearish island: gap up to isolate highs, then gap down
            gap1_up = highs[i - 2] < lows[i - 1] if i >= 2 else False
            gap2_dn = highs[i] < lows[i - 1] if i < n else False
            if gap1_up and gap2_dn and i == n - 1:
                patterns.append({
                    "pattern": "island_reversal_bearish", "direction": "bearish",
                    "status": "confirmed", "confidence": 0.70,
                    "category": "CHART_STRUCTURE",
                    "key_levels": {"island_high": round(float(highs[i - 1]), 4),
                                   "island_low": round(float(lows[i - 1]), 4)},
                    "reason": "isolated candle with gap-up entry and gap-down exit — Bearish Island Reversal",
                })

    # ── Measured move ─────────────────────────────────────────────────────────
    if len(swing_highs) >= 2 and len(swing_lows) >= 2 and n >= 20:
        first_move = swing_highs[-1]["price"] - swing_lows[-2]["price"]
        retrace = swing_highs[-1]["price"] - swing_lows[-1]["price"]
        if first_move > 0 and retrace > 0:
            projected_target = current_price + first_move * 0.618
            patterns.append({
                "pattern": "measured_move_up", "direction": "bullish",
                "status": "forming", "confidence": 0.50,
                "category": "CHART_STRUCTURE",
                "key_levels": {"first_move": round(first_move, 4),
                               "projected_target": round(projected_target, 4)},
                "reason": f"first leg {first_move:.2f}, project {first_move:.2f} extension from current base",
            })
        first_move = swing_lows[-1]["price"] - swing_highs[-2]["price"]
        if first_move < 0:
            projected_target = current_price + first_move * 0.618
            patterns.append({
                "pattern": "measured_move_down", "direction": "bearish",
                "status": "forming", "confidence": 0.50,
                "category": "CHART_STRUCTURE",
                "key_levels": {"first_move": round(first_move, 4),
                               "projected_target": round(projected_target, 4)},
                "reason": f"first leg {first_move:.2f}, project similar leg extension",
            })

    return patterns
