"""
aiem_pattern_engine.py — Unified pattern detection coordinator.

Phase 2 repair:
  - detect_for_ticker broad exception no longer returns pattern_score=0.5;
    returns {"pattern_score": None, "status": "FAILED", "error": ...} instead.
  - Each sub-family exception logs at WARNING level and tracks per-family status.
  - _compute_pattern_score returns None (not 0.5) when no patterns detected
    or no PASS-registry patterns match.
  - detect_all_patterns returns pattern_score=None when no PASS patterns exist
    in the registry (not 0.5).
  - persist_pattern_snapshot() writes to oe_pattern_snapshots (optional call).

Only patterns with registry status=PASS contribute to the pattern_score.
UNTESTED patterns are logged but treated as unavailable weight contributors.
FAIL/disabled patterns are skipped entirely.
"""
from __future__ import annotations
import os
import json
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
) -> Optional[float]:
    """
    Aggregate all detected patterns against the thesis.

    Phase 2 repair: returns None (not 0.5) when:
      - detected is empty (no patterns found)
      - no detected pattern name is in pass_patterns (no PASS-registry matches)
      - total weighted confidence is below the noise floor

    Only returns a float when at least one PASS-status pattern actually ran and
    produced a directional signal.

    thesis: "BULLISH" | "BEARISH" | "NEUTRAL" | "ANY"
    Returns Optional[float] in [0.0, 1.0] or None.
    """
    if not detected:
        return None   # Phase 2: no patterns fired at all — not a neutral default

    total_weight = 0.0
    weighted_agreement = 0.0

    for p in detected:
        name = p.get("pattern", "")
        if name not in pass_patterns:
            continue   # UNTESTED or unlisted — skip
        direction = p.get("direction", "NEUTRAL")
        confidence = float(p.get("confidence", 0.0))

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
        return None   # Phase 2: no PASS patterns matched — not a neutral default

    raw = weighted_agreement / total_weight
    if abs(raw - 0.5) < 0.05:
        # Ambiguous deadband — still a real score (not fabricated), return it
        return 0.5
    return max(0.0, min(1.0, raw))


def detect_all_patterns(
    bars: List[Dict],
    ticker: str = "",
    thesis: str = "NEUTRAL",
) -> Dict[str, Any]:
    """
    Run all 5 pattern families against bars.

    Phase 2 repair:
      - Per-family exceptions log at WARNING, not DEBUG, and set family status.
      - pattern_score is None when no PASS patterns are registered or detected.
      - A broad exception in any family does NOT produce a 0.5 score for that family.

    Returns:
    {
      "pattern_score": Optional[float],   # None if no PASS patterns matched
      "pass_only_score": Optional[float],
      "all_patterns": [...],
      "candlestick": [...],
      "chart_structure": [...],
      "harmonic": [...],
      "wyckoff_vpa": [...],
      "elliott_wave": [...],
      "family_statuses": {"candlestick": "OK"|"FAILED"|"EMPTY", ...},
      "bars_used": int,
      "ticker": str,
      "thesis": str,
      "detected_at": str,
      "status": "OK" | "PARTIAL" | "FAILED",
    }
    """
    detected_at = datetime.datetime.utcnow().isoformat()
    result: Dict[str, Any] = {
        "pattern_score":    None,    # Phase 2: None until a PASS pattern fires
        "pass_only_score":  None,
        "all_patterns":     [],
        "candlestick":      [],
        "chart_structure":  [],
        "harmonic":         [],
        "wyckoff_vpa":      [],
        "elliott_wave":     [],
        "family_statuses":  {},
        "bars_used":        len(bars),
        "ticker":           ticker,
        "thesis":           thesis,
        "detected_at":      detected_at,
        "status":           "OK",
    }

    if len(bars) < 5:
        result["error"]  = "insufficient bars"
        result["status"] = "FAILED"
        return result

    family_ok   = 0
    family_fail = 0

    # ── Candlestick ───────────────────────────────────────────────────────────
    try:
        from candlestick_patterns import detect_patterns as cs_detect
        cs_result = cs_detect(bars)
        pats = cs_result.get("patterns", [])
        result["candlestick"] = pats
        result["family_statuses"]["candlestick"] = "OK" if pats else "EMPTY"
        family_ok += 1
    except Exception as e:
        log.warning(f"[pattern_engine] candlestick FAILED for {ticker}: {e}")
        result["family_statuses"]["candlestick"] = "FAILED"
        family_fail += 1

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
        result["family_statuses"]["chart_structure"] = "OK" if ps_pats else "EMPTY"
        family_ok += 1
    except Exception as e:
        log.warning(f"[pattern_engine] chart_structure FAILED for {ticker}: {e}")
        result["family_statuses"]["chart_structure"] = "FAILED"
        family_fail += 1

    # ── Harmonic ──────────────────────────────────────────────────────────────
    try:
        from aiem_harmonic_patterns import detect_harmonic_patterns
        pats = detect_harmonic_patterns(bars)
        result["harmonic"] = pats
        result["family_statuses"]["harmonic"] = "OK" if pats else "EMPTY"
        family_ok += 1
    except Exception as e:
        log.warning(f"[pattern_engine] harmonic FAILED for {ticker}: {e}")
        result["family_statuses"]["harmonic"] = "FAILED"
        family_fail += 1

    # ── Wyckoff / VPA ─────────────────────────────────────────────────────────
    try:
        from aiem_wyckoff_vpa import detect_wyckoff_vpa_patterns
        pats = detect_wyckoff_vpa_patterns(bars)
        result["wyckoff_vpa"] = pats
        result["family_statuses"]["wyckoff_vpa"] = "OK" if pats else "EMPTY"
        family_ok += 1
    except Exception as e:
        log.warning(f"[pattern_engine] wyckoff_vpa FAILED for {ticker}: {e}")
        result["family_statuses"]["wyckoff_vpa"] = "FAILED"
        family_fail += 1

    # ── Elliott Wave ──────────────────────────────────────────────────────────
    # Elliott Wave is only reported when a genuine wave count was found by
    # the validated algorithm; if the import itself fails this family is FAILED
    # (not faked as a neutral 0.5 contribution).
    try:
        from aiem_elliott_wave import detect_elliott_wave_patterns
        pats = detect_elliott_wave_patterns(bars)
        result["elliott_wave"] = pats
        result["family_statuses"]["elliott_wave"] = "OK" if pats else "EMPTY"
        family_ok += 1
    except Exception as e:
        log.warning(f"[pattern_engine] elliott_wave FAILED for {ticker}: {e}")
        result["family_statuses"]["elliott_wave"] = "FAILED"
        family_fail += 1

    # ── Aggregate ─────────────────────────────────────────────────────────────
    all_pats = (result["candlestick"] + result["chart_structure"]
                + result["harmonic"] + result["wyckoff_vpa"] + result["elliott_wave"])
    result["all_patterns"] = all_pats

    pass_pats = _get_pass_patterns()

    # Phase 2: pass_only_score = None when no PASS patterns matched — not 0.5
    result["pass_only_score"] = _compute_pattern_score(all_pats, thesis, pass_pats)

    # pattern_score = pass_only_score if PASS patterns exist in registry;
    # None if registry is empty (can't distinguish confirmed from unvalidated)
    result["pattern_score"] = result["pass_only_score"] if pass_pats else None

    # Overall status
    if family_fail == 5:
        result["status"] = "FAILED"
    elif family_fail > 0:
        result["status"] = "PARTIAL"
    else:
        result["status"] = "OK"

    return result


def detect_for_ticker(ticker: str, thesis: str = "NEUTRAL", lookback: int = 60) -> Dict[str, Any]:
    """
    Fetch bars from DB and run full pattern detection.

    Phase 2 repair: exception path returns pattern_score=None and status=FAILED
    instead of the former pattern_score=0.5.  Callers must inspect "status"
    before trusting any numeric field.
    """
    try:
        bars = fetch_ohlcv_bars(ticker, lookback)
    except Exception as e:
        log.warning(f"[pattern_engine] fetch_ohlcv_bars FAILED for {ticker}: {e}")
        return {
            "pattern_score": None,     # Phase 2: no longer 0.5 on broad exception
            "status":        "FAILED",
            "error":         str(e),
            "ticker":        ticker,
            "family_statuses": {},
        }
    return detect_all_patterns(bars, ticker=ticker, thesis=thesis)


# ── Optional persistence ────────────────────────────────────────────────────

def persist_pattern_snapshot(
    ticker: str,
    trace_id: str,
    pattern_result: Dict[str, Any],
    scan_date: Optional[datetime.date] = None,
) -> bool:
    """
    Write one row to oe_pattern_snapshots for audit / downstream use.

    Fields persisted:
      trace_id, ticker, scan_date, canonical_id (primary pattern name),
      timeframe="daily", detection_confidence, actionable, influenced_recommendation=False,
      pattern_data (full JSON), regime=None, failure_reason (if status=FAILED).

    Returns True on success, False on error.  Never raises.
    Data immutability: INSERT only (no UPDATE/DELETE).
    """
    try:
        sd = scan_date or datetime.date.today()
        all_pats = pattern_result.get("all_patterns", [])
        score    = pattern_result.get("pattern_score")
        status   = pattern_result.get("status", "OK")
        failure  = pattern_result.get("error") if status == "FAILED" else None

        # Primary pattern: highest-confidence directional one
        directional = [p for p in all_pats if p.get("direction") in ("BULLISH", "BEARISH")]
        best = max(directional, key=lambda p: float(p.get("confidence", 0))) if directional else None
        canonical_id  = best.get("pattern", "") if best else None
        detection_conf = float(best.get("confidence", 0)) if best else None
        invalidation   = best.get("invalidation_level") if best else None
        # Actionable: score is not None and meaningfully directional
        actionable = bool(score is not None and abs(score - 0.5) >= 0.10)

        pattern_data = {
            "pattern_score":    score,
            "pass_only_score":  pattern_result.get("pass_only_score"),
            "all_patterns":     all_pats,
            "family_statuses":  pattern_result.get("family_statuses", {}),
            "thesis":           pattern_result.get("thesis"),
            "bars_used":        pattern_result.get("bars_used"),
            "invalidation_level": invalidation,
        }

        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO oe_pattern_snapshots
                        (trace_id, ticker, scan_date, canonical_id, timeframe,
                         detection_confidence, actionable, influenced_recommendation,
                         pattern_data, failure_reason, captured_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    trace_id, ticker.upper(), sd, canonical_id, "daily",
                    detection_conf, actionable, False,
                    json.dumps(pattern_data), failure,
                ))
            conn.commit()
        return True
    except Exception as e:
        log.warning(f"[pattern_engine] persist_pattern_snapshot {ticker}: {e}")
        return False
