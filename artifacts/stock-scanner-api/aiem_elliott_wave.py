"""
aiem_elliott_wave.py — Elliott Wave pattern detection.

Implements 7 Elliott Wave pattern types:
  1. Impulse Wave (5-wave motive)
  2. Corrective ABC (3-wave)
  3. Zigzag (5-3-5)
  4. Flat (3-3-5)
  5. Triangle (3-3-3-3-3)
  6. Double Three (WXY)
  7. Triple Three (WXYXZ)

IMPORTANT: Elliott Wave counting is inherently probabilistic and involves
labeling ambiguity. This implementation uses rule-based zigzag decomposition
to approximate wave structure. It enforces the three inviolable EW rules:
  Rule 1: Wave 2 never retraces more than 100% of Wave 1
  Rule 2: Wave 3 is never the shortest impulse wave
  Rule 3: Wave 4 never enters the price territory of Wave 1

All patterns are marked "forming" until a completed structure is confirmed.
"""
from __future__ import annotations
import math
from typing import List, Dict, Any, Optional, Tuple

import numpy as np

try:
    from scipy.signal import find_peaks
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Zigzag engine ─────────────────────────────────────────────────────────────

def _zigzag(closes: np.ndarray, threshold_pct: float = 5.0) -> List[Dict]:
    """
    Classic percentage-threshold zigzag. Returns alternating turning points:
    [{"idx": int, "price": float, "kind": "H"|"L"}, ...]
    threshold_pct: minimum % move required to register a new pivot.
    """
    n = len(closes)
    if n < 4:
        return []
    pivots = []
    last_pivot = {"idx": 0, "price": float(closes[0]), "kind": "L"}
    direction = None

    for i in range(1, n):
        p = float(closes[i])
        lp = last_pivot["price"]
        move_up = (p - lp) / lp * 100 if lp > 0 else 0.0
        move_dn = (lp - p) / lp * 100 if lp > 0 else 0.0

        if direction is None:
            if move_up >= threshold_pct:
                pivots.append(last_pivot)
                last_pivot = {"idx": i, "price": p, "kind": "H"}
                direction = "UP"
            elif move_dn >= threshold_pct:
                pivots.append(last_pivot)
                last_pivot = {"idx": i, "price": p, "kind": "L"}
                direction = "DN"
        elif direction == "UP":
            if p > last_pivot["price"]:
                last_pivot = {"idx": i, "price": p, "kind": "H"}
            elif (last_pivot["price"] - p) / last_pivot["price"] * 100 >= threshold_pct:
                pivots.append(last_pivot)
                last_pivot = {"idx": i, "price": p, "kind": "L"}
                direction = "DN"
        else:
            if p < last_pivot["price"]:
                last_pivot = {"idx": i, "price": p, "kind": "L"}
            elif (p - last_pivot["price"]) / last_pivot["price"] * 100 >= threshold_pct:
                pivots.append(last_pivot)
                last_pivot = {"idx": i, "price": p, "kind": "H"}
                direction = "UP"

    pivots.append(last_pivot)
    return pivots


def _leg_sizes(pivots: List[Dict]) -> List[float]:
    """Return signed leg sizes between consecutive pivots."""
    legs = []
    for i in range(1, len(pivots)):
        legs.append(pivots[i]["price"] - pivots[i - 1]["price"])
    return legs


def _retrace_ratio(wave_a: float, wave_b: float) -> Optional[float]:
    if abs(wave_a) < 1e-9:
        return None
    return abs(wave_b) / abs(wave_a)


# ── Wave structure validators ──────────────────────────────────────────────────

def _validate_impulse(pivots: List[Dict]) -> Optional[Dict]:
    """
    Validate 5-wave impulse (motive wave).
    Requires exactly 6 pivots: W0, W1, W2, W3, W4, W5
    Enforces all 3 EW inviolable rules.
    """
    if len(pivots) < 6:
        return None
    pts = pivots[-6:]
    legs = _leg_sizes(pts)
    if len(legs) < 5:
        return None
    w1, w2, w3, w4, w5 = legs[0], legs[1], legs[2], legs[3], legs[4]

    bullish = w1 > 0
    if bullish:
        if not (w1 > 0 and w2 < 0 and w3 > 0 and w4 < 0 and w5 > 0):
            return None
    else:
        if not (w1 < 0 and w2 > 0 and w3 < 0 and w4 > 0 and w5 < 0):
            return None

    r2 = _retrace_ratio(w1, w2)
    if r2 is None or r2 >= 1.0:
        return None

    w1_abs, w3_abs, w5_abs = abs(w1), abs(w3), abs(w5)
    if w3_abs <= min(w1_abs, w5_abs):
        return None

    w4_end = pts[4]["price"]
    w1_start = pts[0]["price"]
    w1_end = pts[1]["price"]
    if bullish:
        if w4_end <= max(w1_start, w1_end):
            overlap = True
        else:
            overlap = False
    else:
        if w4_end >= min(w1_start, w1_end):
            overlap = True
        else:
            overlap = False
    if overlap:
        return None

    direction = "BULLISH" if bullish else "BEARISH"
    return {
        "direction": direction,
        "w1": round(w1, 4), "w2": round(w2, 4), "w3": round(w3, 4),
        "w4": round(w4, 4), "w5": round(w5, 4),
        "wave2_retrace": round(r2, 3),
        "w3_ratio_vs_w1": round(w3_abs / w1_abs, 3),
    }


def _validate_abc(pivots: List[Dict]) -> Optional[Dict]:
    """
    Validate simple ABC corrective wave (3 pivots after 4: 0,A,B,C).
    Requires 4 pivots.
    """
    if len(pivots) < 4:
        return None
    pts = pivots[-4:]
    legs = _leg_sizes(pts)
    if len(legs) < 3:
        return None
    wa, wb, wc = legs[0], legs[1], legs[2]
    if not (abs(wa) > 0 and abs(wb) > 0 and abs(wc) > 0):
        return None
    if wa > 0:
        if not (wa > 0 and wb < 0 and wc > 0):
            return None
        direction = "BULLISH"
    else:
        if not (wa < 0 and wb > 0 and wc < 0):
            return None
        direction = "BEARISH"
    r_b = _retrace_ratio(wa, wb)
    return {
        "direction": direction,
        "wa": round(wa, 4), "wb": round(wb, 4), "wc": round(wc, 4),
        "b_retrace": round(r_b, 3) if r_b else None,
    }


def _validate_zigzag(pivots: List[Dict]) -> Optional[Dict]:
    """
    Zigzag = 5-3-5 structure: impulse A + short corrective B + impulse C.
    B retraces < 61.8% of A; C ≈ or > A.
    Approximated with 4 pivots (A, B, C legs).
    """
    if len(pivots) < 4:
        return None
    pts = pivots[-4:]
    legs = _leg_sizes(pts)
    if len(legs) < 3:
        return None
    wa, wb, wc = legs[0], legs[1], legs[2]
    if not (abs(wa) > 0 and abs(wb) > 0 and abs(wc) > 0):
        return None
    if wa > 0:
        if not (wa > 0 and wb < 0 and wc > 0):
            return None
        direction = "BULLISH"
    else:
        if not (wa < 0 and wb > 0 and wc < 0):
            return None
        direction = "BEARISH"
    r_b = _retrace_ratio(wa, wb)
    if r_b is None or r_b >= 0.618:
        return None
    r_c = abs(wc) / abs(wa)
    if r_c < 0.9:
        return None
    return {
        "direction": direction,
        "b_retrace": round(r_b, 3),
        "c_vs_a": round(r_c, 3),
    }


def _validate_flat(pivots: List[Dict]) -> Optional[Dict]:
    """
    Flat = 3-3-5 structure. B retraces >= 61.8% of A; C ≈ A.
    Regular flat: B near A's origin, C near A's end.
    """
    if len(pivots) < 4:
        return None
    pts = pivots[-4:]
    legs = _leg_sizes(pts)
    if len(legs) < 3:
        return None
    wa, wb, wc = legs[0], legs[1], legs[2]
    if not (abs(wa) > 0 and abs(wb) > 0 and abs(wc) > 0):
        return None
    if wa > 0:
        if not (wa > 0 and wb < 0 and wc > 0):
            return None
        direction = "BULLISH"
    else:
        if not (wa < 0 and wb > 0 and wc < 0):
            return None
        direction = "BEARISH"
    r_b = _retrace_ratio(wa, wb)
    if r_b is None or r_b < 0.618:
        return None
    r_c = abs(wc) / abs(wa)
    if r_c < 0.8 or r_c > 1.25:
        return None
    return {
        "direction": direction,
        "b_retrace": round(r_b, 3),
        "c_vs_a": round(r_c, 3),
    }


def _validate_triangle(pivots: List[Dict]) -> Optional[Dict]:
    """
    Triangle = 5 waves (a-b-c-d-e) with contracting price range.
    Each wave must be smaller than the preceding wave of same polarity.
    """
    if len(pivots) < 6:
        return None
    pts = pivots[-6:]
    legs = _leg_sizes(pts)
    if len(legs) < 5:
        return None
    abs_legs = [abs(l) for l in legs]
    if not (abs_legs[0] > abs_legs[2] > abs_legs[4]):
        return None
    if not (abs_legs[1] > abs_legs[3]):
        return None
    if legs[0] > 0:
        if not all(s > 0 for i, s in enumerate(legs) if i % 2 == 0):
            return None
        direction = "BULLISH"
    else:
        if not all(s < 0 for i, s in enumerate(legs) if i % 2 == 0):
            return None
        direction = "BEARISH"
    return {
        "direction": direction,
        "wave_a": round(legs[0], 4), "wave_b": round(legs[1], 4),
        "wave_c": round(legs[2], 4), "wave_d": round(legs[3], 4),
        "wave_e": round(legs[4], 4),
        "contraction_ratio": round(abs_legs[4] / abs_legs[0], 3),
    }


def _validate_double_three(pivots: List[Dict]) -> Optional[Dict]:
    """
    Double Three (WXY): Two corrective patterns (W and Y) connected by X.
    Approximated as 6 pivots with W and Y being same-direction legs.
    """
    if len(pivots) < 7:
        return None
    pts = pivots[-7:]
    legs = _leg_sizes(pts)
    if len(legs) < 6:
        return None
    ww, wx, wy = sum(legs[0:2]), legs[2], sum(legs[3:5])
    if abs(wx) < 1e-9:
        return None
    r_x = _retrace_ratio(ww, wx)
    if ww > 0:
        direction = "BULLISH"
    else:
        direction = "BEARISH"
    if r_x is None or r_x < 0.3 or r_x > 1.1:
        return None
    return {
        "direction": direction,
        "w_size": round(ww, 4), "x_retrace": round(r_x, 3),
        "y_size": round(wy, 4),
    }


def _validate_triple_three(pivots: List[Dict]) -> Optional[Dict]:
    """
    Triple Three (WXYXZ): Three corrective patterns connected by two X waves.
    Needs 10 pivots.
    """
    if len(pivots) < 10:
        return None
    pts = pivots[-10:]
    legs = _leg_sizes(pts)
    if len(legs) < 9:
        return None
    ww = sum(legs[0:3])
    wx1 = legs[3]
    wy = sum(legs[4:6])
    wx2 = legs[6]
    wz = sum(legs[7:9])
    if ww > 0:
        direction = "BULLISH"
    else:
        direction = "BEARISH"
    r_x1 = _retrace_ratio(ww, wx1)
    r_x2 = _retrace_ratio(wy, wx2)
    if r_x1 is None or r_x2 is None:
        return None
    if not (0.3 <= r_x1 <= 1.1 and 0.3 <= r_x2 <= 1.1):
        return None
    return {
        "direction": direction,
        "w_size": round(ww, 4), "x1_retrace": round(r_x1, 3),
        "y_size": round(wy, 4), "x2_retrace": round(r_x2, 3),
        "z_size": round(wz, 4),
    }


# ── Public API ────────────────────────────────────────────────────────────────

_PATTERN_SPECS = [
    ("elliott_impulse",      "MOTIVE",     _validate_impulse,      0.62, "5-wave motive impulse"),
    ("elliott_abc",          "CORRECTIVE", _validate_abc,          0.50, "3-wave corrective ABC"),
    ("elliott_zigzag",       "CORRECTIVE", _validate_zigzag,       0.55, "5-3-5 zigzag, B<61.8% of A"),
    ("elliott_flat",         "CORRECTIVE", _validate_flat,         0.52, "3-3-5 flat, B>=61.8% of A"),
    ("elliott_triangle",     "CORRECTIVE", _validate_triangle,     0.55, "5-wave contracting triangle"),
    ("elliott_double_three", "CORRECTIVE", _validate_double_three, 0.48, "WXY double-three complex correction"),
    ("elliott_triple_three", "CORRECTIVE", _validate_triple_three, 0.45, "WXYXZ triple-three complex correction"),
]


def detect_elliott_wave_patterns(bars: List[Dict[str, Any]],
                                  zigzag_pct: float = 5.0) -> List[Dict[str, Any]]:
    """
    Detect Elliott Wave patterns from OHLCV bars (oldest first).
    Uses zigzag decomposition; returns list of PatternResult dicts.

    Note: Results are labelled "forming" — EW structures are probabilistic
    and should not be used as standalone trade signals without confirmation.
    """
    if len(bars) < 20:
        return []

    closes = np.array([b["close"] for b in bars], dtype=float)
    n = len(bars)

    pivots = _zigzag(closes, threshold_pct=zigzag_pct)
    if len(pivots) < 4:
        return []

    results = []
    for name, wave_type, validator, base_conf, description in _PATTERN_SPECS:
        try:
            detail = validator(pivots)
            if detail is None:
                continue
            direction = detail.get("direction", "NEUTRAL")
            status = "forming"
            confidence = base_conf
            results.append({
                "pattern": name,
                "category": "ELLIOTT_WAVE",
                "wave_type": wave_type,
                "direction": direction,
                "status": status,
                "confidence": round(confidence, 2),
                "key_levels": {
                    "last_pivot_price": round(pivots[-1]["price"], 4),
                    "pivot_count": len(pivots),
                },
                "wave_detail": detail,
                "reason": f"{description} — {direction.lower()} structure detected on {len(pivots)} zigzag pivots",
                "bar_index": n - 1,
                "note": "Elliott Wave labeling is probabilistic; confirm with volume and momentum",
            })
        except Exception:
            pass

    return results
