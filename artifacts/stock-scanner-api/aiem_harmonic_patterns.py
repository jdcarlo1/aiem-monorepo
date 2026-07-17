"""
aiem_harmonic_patterns.py — Harmonic pattern detection.

Implements all 9 harmonic patterns using XABCD Fibonacci ratio validation:
  Gartley, Bat, Butterfly, Crab, Deep Crab, Shark, Cypher, AB=CD, Three Drives

Algorithm:
1. Extract swing highs/lows from OHLCV data
2. Build XABCD point sequences from recent swings
3. Validate Fibonacci ratios at each leg
4. Return confirmed/forming patterns with key levels

Ratio reference (PRZ = Potential Reversal Zone):
  Gartley:    XAB=0.618,  ABC=0.382-0.886, BCD=1.13-1.618, XAD=0.786
  Bat:        XAB=0.382-0.500, ABC=0.382-0.886, BCD=1.618-2.618, XAD=0.886
  Butterfly:  XAB=0.786,  ABC=0.382-0.886, BCD=1.618-2.24,  XAD=1.272-1.618
  Crab:       XAB=0.382-0.618, ABC=0.382-0.886, BCD=2.618-3.618, XAD=1.618
  Deep Crab:  XAB=0.886,  ABC=0.382-0.886, BCD=2.0-3.618,   XAD=1.618
  Shark:      ABC=1.13-1.618,  BCD=1.618-2.24, XAD=0.886-1.13
  Cypher:     XAB=0.382-0.618, ABC=1.13-1.41, BCD=0.382-0.618, XAD=0.786
  AB=CD:      BC=0.382-0.886 retrace of AB, CD ≈ AB length
  Three Drives: 3 impulse waves with 0.618/0.786 retracements and 1.272/1.618 extensions
"""
from __future__ import annotations
import math
from typing import List, Dict, Any, Optional, Tuple

try:
    from scipy.signal import find_peaks
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

import numpy as np


# ── Fibonacci ratio tolerance ─────────────────────────────────────────────────
_TOL = 0.10  # ±10% of ratio for matching


def _ratio_ok(actual: float, target: float, tol: float = _TOL) -> bool:
    return abs(actual - target) <= tol * target


def _ratio_in(actual: float, lo: float, hi: float, tol: float = _TOL) -> bool:
    return (lo * (1 - tol)) <= actual <= (hi * (1 + tol))


def _retracement(xa: float, ab: float) -> Optional[float]:
    """Retrace of AB relative to XA. xa/ab are signed leg lengths."""
    if abs(xa) < 1e-9:
        return None
    return abs(ab) / abs(xa)


def _extension(ab: float, bc: float, cd: float) -> Optional[float]:
    """CD extension relative to BC projected from B."""
    if abs(bc) < 1e-9:
        return None
    return abs(cd) / abs(bc)


# ── Swing point extraction ────────────────────────────────────────────────────

def _find_swings(highs: np.ndarray, lows: np.ndarray,
                 min_distance: int = 3) -> Tuple[List[Dict], List[Dict]]:
    """Return swing highs/lows as [{idx, price}] lists."""
    n = len(highs)
    if n < min_distance * 2 + 1:
        return [], []

    if _HAS_SCIPY:
        try:
            ph, _ = find_peaks(highs, distance=min_distance)
            pl, _ = find_peaks(-lows, distance=min_distance)
            peaks = [{"idx": int(i), "price": float(highs[i])} for i in ph]
            troughs = [{"idx": int(i), "price": float(lows[i])} for i in pl]
            return peaks, troughs
        except Exception:
            pass

    peaks = [{"idx": i, "price": float(highs[i])}
             for i in range(min_distance, n - min_distance)
             if highs[i] == max(highs[max(0, i - min_distance):i + min_distance + 1])]
    troughs = [{"idx": i, "price": float(lows[i])}
               for i in range(min_distance, n - min_distance)
               if lows[i] == min(lows[max(0, i - min_distance):i + min_distance + 1])]
    return peaks, troughs


def _interleave_swings(peaks: List[Dict], troughs: List[Dict]) -> List[Dict]:
    """Merge peaks and troughs sorted by index."""
    merged = [(p["idx"], p["price"], "H") for p in peaks] + \
             [(t["idx"], t["price"], "L") for t in troughs]
    merged.sort(key=lambda x: x[0])
    result = []
    for idx, price, kind in merged:
        if result and result[-1]["kind"] == kind:
            if kind == "H" and price > result[-1]["price"]:
                result[-1] = {"idx": idx, "price": price, "kind": kind}
            elif kind == "L" and price < result[-1]["price"]:
                result[-1] = {"idx": idx, "price": price, "kind": kind}
        else:
            result.append({"idx": idx, "price": price, "kind": kind})
    return result


def _build_xabcd(swings: List[Dict], bullish: bool) -> Optional[Tuple]:
    """
    Pull XABCD points from the last 5 alternating swings.
    Bullish: X=low, A=high, B=low, C=high, D=low (PRZ at D = buy zone)
    Bearish: X=high, A=low, B=high, C=low, D=high (PRZ at D = sell zone)
    """
    if len(swings) < 5:
        return None
    pts = swings[-5:]
    if bullish:
        if not (pts[0]["kind"] == "L" and pts[1]["kind"] == "H"
                and pts[2]["kind"] == "L" and pts[3]["kind"] == "H"
                and pts[4]["kind"] == "L"):
            return None
    else:
        if not (pts[0]["kind"] == "H" and pts[1]["kind"] == "L"
                and pts[2]["kind"] == "H" and pts[3]["kind"] == "L"
                and pts[4]["kind"] == "H"):
            return None
    X, A, B, C, D = [p["price"] for p in pts]
    return X, A, B, C, D


def _leg(p1: float, p2: float) -> float:
    return p2 - p1


# ── Pattern validators ────────────────────────────────────────────────────────

def _check_gartley(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    XA = _leg(X, A)
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    XD = _leg(X, D)
    if abs(XA) < 1e-9:
        return None
    r_xab = abs(AB) / abs(XA)
    r_abc = abs(BC) / abs(AB) if abs(AB) > 1e-9 else None
    r_bcd = abs(CD) / abs(BC) if abs(BC) > 1e-9 else None
    r_xad = abs(XD) / abs(XA)
    if (r_abc is None or r_bcd is None):
        return None
    if (_ratio_ok(r_xab, 0.618)
            and _ratio_in(r_abc, 0.382, 0.886)
            and _ratio_in(r_bcd, 1.130, 1.618)
            and _ratio_ok(r_xad, 0.786)):
        return {"xab": round(r_xab, 3), "abc": round(r_abc, 3),
                "bcd": round(r_bcd, 3), "xad": round(r_xad, 3)}
    return None


def _check_bat(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    XA = _leg(X, A)
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    XD = _leg(X, D)
    if abs(XA) < 1e-9 or abs(AB) < 1e-9 or abs(BC) < 1e-9:
        return None
    r_xab = abs(AB) / abs(XA)
    r_abc = abs(BC) / abs(AB)
    r_bcd = abs(CD) / abs(BC)
    r_xad = abs(XD) / abs(XA)
    if (_ratio_in(r_xab, 0.382, 0.500)
            and _ratio_in(r_abc, 0.382, 0.886)
            and _ratio_in(r_bcd, 1.618, 2.618)
            and _ratio_ok(r_xad, 0.886)):
        return {"xab": round(r_xab, 3), "abc": round(r_abc, 3),
                "bcd": round(r_bcd, 3), "xad": round(r_xad, 3)}
    return None


def _check_butterfly(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    XA = _leg(X, A)
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    XD = _leg(X, D)
    if abs(XA) < 1e-9 or abs(AB) < 1e-9 or abs(BC) < 1e-9:
        return None
    r_xab = abs(AB) / abs(XA)
    r_abc = abs(BC) / abs(AB)
    r_bcd = abs(CD) / abs(BC)
    r_xad = abs(XD) / abs(XA)
    if (_ratio_ok(r_xab, 0.786)
            and _ratio_in(r_abc, 0.382, 0.886)
            and _ratio_in(r_bcd, 1.618, 2.240)
            and _ratio_in(r_xad, 1.272, 1.618)):
        return {"xab": round(r_xab, 3), "abc": round(r_abc, 3),
                "bcd": round(r_bcd, 3), "xad": round(r_xad, 3)}
    return None


def _check_crab(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    XA = _leg(X, A)
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    XD = _leg(X, D)
    if abs(XA) < 1e-9 or abs(AB) < 1e-9 or abs(BC) < 1e-9:
        return None
    r_xab = abs(AB) / abs(XA)
    r_abc = abs(BC) / abs(AB)
    r_bcd = abs(CD) / abs(BC)
    r_xad = abs(XD) / abs(XA)
    if (_ratio_in(r_xab, 0.382, 0.618)
            and _ratio_in(r_abc, 0.382, 0.886)
            and _ratio_in(r_bcd, 2.618, 3.618)
            and _ratio_ok(r_xad, 1.618)):
        return {"xab": round(r_xab, 3), "abc": round(r_abc, 3),
                "bcd": round(r_bcd, 3), "xad": round(r_xad, 3)}
    return None


def _check_deep_crab(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    XA = _leg(X, A)
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    XD = _leg(X, D)
    if abs(XA) < 1e-9 or abs(AB) < 1e-9 or abs(BC) < 1e-9:
        return None
    r_xab = abs(AB) / abs(XA)
    r_abc = abs(BC) / abs(AB)
    r_bcd = abs(CD) / abs(BC)
    r_xad = abs(XD) / abs(XA)
    if (_ratio_ok(r_xab, 0.886)
            and _ratio_in(r_abc, 0.382, 0.886)
            and _ratio_in(r_bcd, 2.000, 3.618)
            and _ratio_ok(r_xad, 1.618)):
        return {"xab": round(r_xab, 3), "abc": round(r_abc, 3),
                "bcd": round(r_bcd, 3), "xad": round(r_xad, 3)}
    return None


def _check_shark(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    """Shark uses OXABCD; we use the XABCD window but skip XAB ratio."""
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    XD = _leg(X, D)
    XA = _leg(X, A)
    if abs(BC) < 1e-9 or abs(AB) < 1e-9 or abs(XA) < 1e-9:
        return None
    r_abc = abs(BC) / abs(AB)
    r_bcd = abs(CD) / abs(BC)
    r_xad = abs(XD) / abs(XA)
    if (_ratio_in(r_abc, 1.130, 1.618)
            and _ratio_in(r_bcd, 1.618, 2.240)
            and _ratio_in(r_xad, 0.886, 1.130)):
        return {"abc": round(r_abc, 3), "bcd": round(r_bcd, 3), "xad": round(r_xad, 3)}
    return None


def _check_cypher(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    XA = _leg(X, A)
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    XC = _leg(X, C)
    XA_len = abs(XA)
    if XA_len < 1e-9 or abs(AB) < 1e-9 or abs(BC) < 1e-9 or abs(XC) < 1e-9:
        return None
    r_xab = abs(AB) / XA_len
    r_abc = abs(BC) / abs(AB)
    r_bcd = abs(CD) / abs(BC)
    r_xcd = abs(CD) / abs(XC)
    if (_ratio_in(r_xab, 0.382, 0.618)
            and _ratio_in(r_abc, 1.130, 1.410)
            and _ratio_in(r_bcd, 0.382, 0.618)
            and _ratio_ok(r_xcd, 0.786)):
        return {"xab": round(r_xab, 3), "abc": round(r_abc, 3),
                "bcd": round(r_bcd, 3), "xcd": round(r_xcd, 3)}
    return None


def _check_abcd(X, A, B, C, D, bullish: bool) -> Optional[Dict]:
    """AB=CD: requires BC retrace of AB and CD ≈ AB in length."""
    AB = _leg(A, B)
    BC = _leg(B, C)
    CD = _leg(C, D)
    if abs(AB) < 1e-9 or abs(BC) < 1e-9:
        return None
    r_bc = abs(BC) / abs(AB)
    r_cd_ab = abs(CD) / abs(AB)
    if (_ratio_in(r_bc, 0.382, 0.886)
            and _ratio_ok(r_cd_ab, 1.0)):
        return {"bc_ab": round(r_bc, 3), "cd_ab": round(r_cd_ab, 3)}
    return None


def _check_three_drives(swings: List[Dict], bullish: bool) -> Optional[Dict]:
    """
    Three Drives: three impulse waves with 0.618/0.786 retracements
    and 1.272/1.618 extensions between drives.
    Requires at least 7 swing points (drive1, retrace1, drive2, retrace2, drive3).
    """
    if len(swings) < 7:
        return None
    pts = swings[-7:]
    if bullish:
        if not all(pts[i]["kind"] == ("L" if i % 2 == 0 else "H") for i in range(7)):
            return None
    else:
        if not all(pts[i]["kind"] == ("H" if i % 2 == 0 else "L") for i in range(7)):
            return None
    prices = [p["price"] for p in pts]
    drive1 = abs(prices[1] - prices[0])
    ret1 = abs(prices[2] - prices[1])
    drive2 = abs(prices[3] - prices[2])
    ret2 = abs(prices[4] - prices[3])
    drive3 = abs(prices[5] - prices[4])
    if drive1 < 1e-9 or drive2 < 1e-9 or ret1 < 1e-9:
        return None
    r_ret1 = ret1 / drive1
    r_drive2 = drive2 / drive1
    r_ret2 = ret2 / drive2 if drive2 > 1e-9 else None
    r_drive3 = drive3 / drive2 if drive2 > 1e-9 else None
    if (r_ret2 is None or r_drive3 is None):
        return None
    if (_ratio_in(r_ret1, 0.618, 0.786)
            and _ratio_in(r_drive2, 1.272, 1.618)
            and _ratio_in(r_ret2, 0.618, 0.786)
            and _ratio_in(r_drive3, 1.272, 1.618)):
        return {"ret1": round(r_ret1, 3), "drive2": round(r_drive2, 3),
                "ret2": round(r_ret2, 3), "drive3": round(r_drive3, 3)}
    return None


# ── Public API ────────────────────────────────────────────────────────────────

_XABCD_CHECKS = [
    ("gartley",   _check_gartley),
    ("bat",       _check_bat),
    ("butterfly", _check_butterfly),
    ("crab",      _check_crab),
    ("deep_crab", _check_deep_crab),
    ("shark",     _check_shark),
    ("cypher",    _check_cypher),
    ("abcd",      _check_abcd),
]

_DIRECTION_MAP = {
    "gartley":   {"bullish": "BULLISH", "bearish": "BEARISH"},
    "bat":       {"bullish": "BULLISH", "bearish": "BEARISH"},
    "butterfly": {"bullish": "BULLISH", "bearish": "BEARISH"},
    "crab":      {"bullish": "BULLISH", "bearish": "BEARISH"},
    "deep_crab": {"bullish": "BULLISH", "bearish": "BEARISH"},
    "shark":     {"bullish": "BULLISH", "bearish": "BEARISH"},
    "cypher":    {"bullish": "BULLISH", "bearish": "BEARISH"},
    "abcd":      {"bullish": "BULLISH", "bearish": "BEARISH"},
}

_CONFIDENCE = {
    "gartley": 0.65, "bat": 0.65, "butterfly": 0.60, "crab": 0.62,
    "deep_crab": 0.60, "shark": 0.58, "cypher": 0.62, "abcd": 0.55,
    "three_drives": 0.60,
}


def detect_harmonic_patterns(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Detect all 9 harmonic patterns from OHLCV bars (oldest first).
    Returns list of PatternResult dicts.
    """
    if len(bars) < 20:
        return []

    highs = np.array([b["high"] for b in bars], dtype=float)
    lows = np.array([b["low"] for b in bars], dtype=float)
    n = len(bars)
    current_price = float(bars[-1]["close"])

    peaks, troughs = _find_swings(highs, lows, min_distance=3)
    if not peaks or not troughs:
        return []

    swings = _interleave_swings(peaks, troughs)
    results = []

    for bullish in (True, False):
        xabcd = _build_xabcd(swings, bullish)
        if xabcd is None:
            continue
        X, A, B, C, D = xabcd
        for name, checker in _XABCD_CHECKS:
            ratios = checker(X, A, B, C, D, bullish)
            if ratios is None:
                continue
            direction = "BULLISH" if bullish else "BEARISH"
            prz = D
            distance_pct = (current_price - prz) / prz * 100 if prz else None
            at_prz = abs(distance_pct) <= 3.0 if distance_pct is not None else False
            status = "confirmed" if at_prz else "forming"
            results.append({
                "pattern": f"harmonic_{name}",
                "category": "HARMONIC",
                "direction": direction,
                "status": status,
                "confidence": round(_CONFIDENCE.get(name, 0.55) + (0.05 if at_prz else 0.0), 2),
                "key_levels": {
                    "X": round(X, 4), "A": round(A, 4), "B": round(B, 4),
                    "C": round(C, 4), "D_prz": round(D, 4),
                },
                "ratios": ratios,
                "reason": (f"{'Bullish' if bullish else 'Bearish'} {name.replace('_', ' ').title()} — "
                           f"PRZ at {prz:.2f}, price {'at PRZ' if at_prz else f'{distance_pct:+.1f}% away'}"),
                "bar_index": n - 1,
            })

    # Three Drives (needs 7 swings)
    for bullish in (True, False):
        ratios = _check_three_drives(swings, bullish)
        if ratios:
            direction = "BULLISH" if bullish else "BEARISH"
            results.append({
                "pattern": "harmonic_three_drives",
                "category": "HARMONIC",
                "direction": direction,
                "status": "forming",
                "confidence": _CONFIDENCE["three_drives"],
                "key_levels": {},
                "ratios": ratios,
                "reason": f"{'Bullish' if bullish else 'Bearish'} Three Drives — three equal extensions forming",
                "bar_index": n - 1,
            })

    return results
