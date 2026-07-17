"""
aiem_pattern_engine.py — Unified pattern detection coordinator.

Pulls OHLCV bars from polygon_market_daily, runs all 5 pattern families
(candlestick, chart structure, harmonic, Wyckoff/VPA, Elliott Wave),
and computes a single pattern_score [0.0, 1.0] relative to a thesis:
  - 0.0  = strong contra-thesis patterns present
  - 0.5  = neutral / no confirmed patterns
  - 1.0  = strong confirming patterns present

Only patterns with registry status=PASS contribute to the pattern_score.
UNTESTED patterns are logged but treated as neutral (0.5).
FAIL/disabled patterns are skipped entirely.

This is the single import that aiem_strat_scheduler.py needs.
"""
from __future__ import annotations
import os
import datetime
import logging
from typing import List, Dict, Any, Optional

import psycopg2

log = logging.getLogger("aiem.pattern_engine")

_DB_URL = os.environ.get("DATABASE_URL", "")

# Cache pass-pattern names to avoid a DB hit every job
_PASS_PATTERNS_CACHE: Optional[List[str]] = None
_PASS_PATTERNS_TTL: Optional[datetime.datetime] = None
_PASS_PATTERNS_TTL_SECONDS = 300


def _get_pass_patterns() -> List[str]:
    global _PASS_PATTERNS_CACHE, _PASS_PATTERNS_TTL
    now = datetime.datetime.utcnow()
    if (_PASS_PATTERNS_CACHE is not None
            and _PASS_PATTERNS_TTL
            and (now - _PASS_PATTERNS_TTL).total_seconds() < _PASS_PATTERNS_TTL_SECONDS):
        return _PASS_PATTERNS_CACHE
    try:
        conn = psycopg2.connect(_DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pattern_name FROM aiem_pattern_registry
                    WHERE enabled=TRUE AND status='PASS'
                """)
                names = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        _PASS_PATTERNS_CACHE = names
        _PASS_PATTERNS_TTL = now
        return names
    except Exception:
        return _PASS_PATTERNS_CACHE or []


def fetch_ohlcv_bars(ticker: str, lookback: int = 60) -> List[Dict]:
    """
    Pull historical OHLCV bars from polygon_market_daily (oldest first).
    Returns list of {"date", "open", "high", "low", "close", "volume"} dicts.
    """
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT scan_date, open_price, high_price, low_price, close_price,
                       COALESCE(volume, 0) AS volume
                FROM polygon_market_daily
                WHERE ticker = %s AND open_price IS NOT NULL
                ORDER BY scan_date DESC
                LIMIT %s
            """, (ticker, lookback))
            rows = cur.fetchall()
    finally:
        conn.close()
    bars = [{"date": str(r[0]), "open": r[1], "high": r[2], "low": r[3],
              "close": r[4], "volume": r[5]}
            for r in reversed(rows)]
    return bars


def _compute_pattern_score(
    detected: List[Dict], thesis: str, pass_patterns: List[str]
) -> float:
    """
    Aggregate all detected patterns against the thesis.
    - Only PASS-status patterns (by name lookup against pass_patterns) affect score.
    - UNTESTED/FAIL contribute 0 weight.
    - thesis: "BULLISH" | "BEARISH" | "NEUTRAL" | "ANY"
    Returns float [0.0, 1.0] where 0.5 = neutral.
    """
    if not detected:
        return 0.5

    total_weight = 0.0
    weighted_agreement = 0.0

    for p in detected:
        name = p.get("pattern", "")
        if name not in pass_patterns:
            continue
        direction = p.get("direction", "NEUTRAL")
        confidence = float(p.get("confidence", 0.5))

        if thesis in ("NEUTRAL", "ANY") or direction == "NEUTRAL":
            agreement = 0.5
        elif direction == thesis:
            agreement = 1.0
        elif direction in ("BULLISH", "BEARISH") and direction != thesis:
            agreement = 0.0
        else:
            agreement = 0.5

        weighted_agreement += confidence * agreement
        total_weight += confidence

    if total_weight < 1e-9:
        return 0.5

    raw = weighted_agreement / total_weight
    if abs(raw - 0.5) < 0.05:
        return 0.5
    return max(0.0, min(1.0, raw))


def detect_all_patterns(
    bars: List[Dict],
    ticker: str = "",
    thesis: str = "NEUTRAL",
) -> Dict[str, Any]:
    """
    Run all 5 pattern families against bars.
    Returns:
    {
      "pattern_score": float,   # 0=contra, 0.5=neutral, 1=confirming vs thesis
      "pass_only_score": float, # score using only PASS patterns
      "all_patterns": [...],    # every triggered pattern across all families
      "candlestick": [...],
      "chart_structure": [...],
      "harmonic": [...],
      "wyckoff_vpa": [...],
      "elliott_wave": [...],
      "bars_used": int,
      "ticker": str,
      "thesis": str,
      "detected_at": str,
    }
    """
    result: Dict[str, Any] = {
        "pattern_score": 0.5,
        "pass_only_score": 0.5,
        "all_patterns": [],
        "candlestick": [],
        "chart_structure": [],
        "harmonic": [],
        "wyckoff_vpa": [],
        "elliott_wave": [],
        "bars_used": len(bars),
        "ticker": ticker,
        "thesis": thesis,
        "detected_at": datetime.datetime.utcnow().isoformat(),
    }

    if len(bars) < 5:
        result["error"] = "insufficient bars"
        return result

    # ── Candlestick ───────────────────────────────────────────────────────────
    try:
        from candlestick_patterns import detect_patterns as cs_detect
        cs_result = cs_detect(bars)
        result["candlestick"] = cs_result.get("patterns", [])
    except Exception as e:
        log.debug(f"candlestick error: {e}")

    # ── Chart structure ───────────────────────────────────────────────────────
    try:
        import numpy as np
        from price_structure_patterns import (
            find_swing_points, classify_chart_patterns, _atr
        )
        highs  = [b["high"] for b in bars]
        lows   = [b["low"]  for b in bars]
        closes = [b["close"] for b in bars]
        h_arr = np.array(highs,  dtype=float)
        l_arr = np.array(lows,   dtype=float)
        c_arr = np.array(closes, dtype=float)
        atr_v = _atr(h_arr, l_arr, c_arr)
        sh, sl, atr_v = find_swing_points(h_arr, l_arr, c_arr, atr_val=atr_v)
        ps_pats = classify_chart_patterns(sh, sl, c_arr, h_arr, l_arr, atr_v)
        for p in ps_pats:
            if "category" not in p:
                p["category"] = "CHART_STRUCTURE"
            if "bar_index" not in p:
                p["bar_index"] = len(bars) - 1
        result["chart_structure"] = ps_pats
    except Exception as e:
        log.debug(f"chart structure error: {e}")

    # ── Harmonic ──────────────────────────────────────────────────────────────
    try:
        from aiem_harmonic_patterns import detect_harmonic_patterns
        result["harmonic"] = detect_harmonic_patterns(bars)
    except Exception as e:
        log.debug(f"harmonic error: {e}")

    # ── Wyckoff / VPA ─────────────────────────────────────────────────────────
    try:
        from aiem_wyckoff_vpa import detect_wyckoff_vpa_patterns
        result["wyckoff_vpa"] = detect_wyckoff_vpa_patterns(bars)
    except Exception as e:
        log.debug(f"wyckoff/vpa error: {e}")

    # ── Elliott Wave ──────────────────────────────────────────────────────────
    try:
        from aiem_elliott_wave import detect_elliott_wave_patterns
        result["elliott_wave"] = detect_elliott_wave_patterns(bars)
    except Exception as e:
        log.debug(f"elliott wave error: {e}")

    # ── Aggregate ─────────────────────────────────────────────────────────────
    all_pats = (result["candlestick"] + result["chart_structure"]
                + result["harmonic"] + result["wyckoff_vpa"] + result["elliott_wave"])
    result["all_patterns"] = all_pats

    pass_pats = _get_pass_patterns()
    result["pass_only_score"] = _compute_pattern_score(all_pats, thesis, pass_pats)

    # Pattern score = pass-only score when pass patterns exist; else neutral
    result["pattern_score"] = result["pass_only_score"] if pass_pats else 0.5

    return result


def detect_for_ticker(ticker: str, thesis: str = "NEUTRAL", lookback: int = 60) -> Dict[str, Any]:
    """Fetch bars from DB and run full pattern detection. Convenience wrapper."""
    try:
        bars = fetch_ohlcv_bars(ticker, lookback)
    except Exception as e:
        return {"pattern_score": 0.5, "error": str(e), "ticker": ticker}
    return detect_all_patterns(bars, ticker=ticker, thesis=thesis)
