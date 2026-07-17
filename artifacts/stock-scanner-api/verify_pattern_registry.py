"""
verify_pattern_registry.py — Backtest-based pattern registry verification.

Tests each enabled detector against historical data from polygon_market_daily.
For each pattern, computes on a walk-forward out-of-sample basis:
  - Precision (when pattern fires, how often is forward return positive?)
  - Recall (of all positive-return events after a pattern setup, how many were caught?)
  - False Positive Rate (pattern fires but forward return negative)
  - False Negative Rate (positive return after setup but pattern missed)

Writes results to aiem_pattern_registry (updates status, precision, recall, fpr, fnr).

Usage:
  python verify_pattern_registry.py [--force] [--category CANDLESTICK]

Results:
  PASS  = precision >= 0.52 AND backtest_n >= 30
  FAIL  = precision < 0.48 AND backtest_n >= 30
  UNTESTED = backtest_n < 30 (insufficient data)
"""
from __future__ import annotations
import os
import sys
import argparse
import datetime
import logging
from typing import List, Dict, Optional

import psycopg2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from aiem_pattern_registry import (
    get_registry, update_pattern_test_result, ensure_table, build_registry
)

log = logging.getLogger("verify_patterns")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_DB_URL = os.environ.get("DATABASE_URL", "")
_PASS_PRECISION = 0.52
_FAIL_PRECISION = 0.48
_MIN_N = 30
_FORWARD_DAYS = 3


def _fetch_all_ohlcv(lookback_days: int = 365) -> Dict[str, List[Dict]]:
    """
    Pull all tickers with sufficient data from polygon_market_daily.
    Returns {ticker: [bars oldest-first]}.
    """
    cutoff = datetime.date.today() - datetime.timedelta(days=lookback_days)
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, scan_date, open_price, high_price, low_price, close_price,
                       COALESCE(volume, 0) AS volume
                FROM polygon_market_daily
                WHERE scan_date >= %s AND open_price IS NOT NULL
                ORDER BY ticker, scan_date ASC
            """, (cutoff,))
            rows = cur.fetchall()
    finally:
        conn.close()

    from collections import defaultdict
    data: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        ticker, date, o, h, l, c, v = r
        data[ticker].append({"date": str(date), "open": o, "high": h,
                              "low": l, "close": c, "volume": v})
    return dict(data)


def _candlestick_fires(pattern_name: str, bars: List[Dict]) -> bool:
    """Check if pattern fires on the most-recent bar."""
    try:
        from candlestick_patterns import detect_patterns
        result = detect_patterns(bars)
        fired_names = {p["pattern"] for p in result.get("patterns", [])}
        return pattern_name in fired_names
    except Exception:
        return False


def _chart_structure_fires(pattern_name: str, bars: List[Dict]) -> bool:
    """Check if chart structure pattern fires given current bars."""
    try:
        import numpy as np
        from price_structure_patterns import find_swing_points, classify_chart_patterns, _atr
        h = np.array([b["high"] for b in bars], dtype=float)
        l = np.array([b["low"] for b in bars], dtype=float)
        c = np.array([b["close"] for b in bars], dtype=float)
        atr_v = _atr(h, l, c)
        sh, sl, atr_v = find_swing_points(h, l, c, atr_val=atr_v)
        pats = classify_chart_patterns(sh, sl, c, h, l, atr_v)
        fired = {p["pattern"] for p in pats}
        return pattern_name in fired
    except Exception:
        return False


def _harmonic_fires(pattern_name: str, bars: List[Dict]) -> bool:
    try:
        from aiem_harmonic_patterns import detect_harmonic_patterns
        pats = detect_harmonic_patterns(bars)
        fired = {p["pattern"] for p in pats}
        return pattern_name in fired
    except Exception:
        return False


def _wyckoff_fires(pattern_name: str, bars: List[Dict]) -> bool:
    try:
        from aiem_wyckoff_vpa import detect_wyckoff_vpa_patterns
        pats = detect_wyckoff_vpa_patterns(bars)
        fired = {p["pattern"] for p in pats}
        return pattern_name in fired
    except Exception:
        return False


def _elliott_fires(pattern_name: str, bars: List[Dict]) -> bool:
    try:
        from aiem_elliott_wave import detect_elliott_wave_patterns
        pats = detect_elliott_wave_patterns(bars)
        fired = {p["pattern"] for p in pats}
        return pattern_name in fired
    except Exception:
        return False


_FIRE_FNS = {
    "CANDLESTICK":    _candlestick_fires,
    "CHART_STRUCTURE": _chart_structure_fires,
    "HARMONIC":       _harmonic_fires,
    "WYCKOFF":        _wyckoff_fires,
    "VPA":            _wyckoff_fires,
    "ELLIOTT_WAVE":   _elliott_fires,
}


def _forward_return(bars: List[Dict], idx: int, forward_days: int = 3) -> Optional[float]:
    """Return pct change from close[idx] to close[idx+forward_days]."""
    if idx + forward_days >= len(bars):
        return None
    entry = bars[idx]["close"]
    exit_ = bars[idx + forward_days]["close"]
    if entry <= 0:
        return None
    return (exit_ - entry) / entry


def _direction_sign(pattern_name: str, registry: List[Dict]) -> int:
    """Return +1 for bullish pattern, -1 for bearish, 0 for neutral."""
    for r in registry:
        if r["pattern_name"] == pattern_name:
            d = r.get("direction", "NEUTRAL")
            if d == "BULLISH":
                return 1
            if d == "BEARISH":
                return -1
    return 0


def backtest_pattern(
    pattern_name: str,
    category: str,
    direction_sign: int,
    all_ticker_bars: Dict[str, List[Dict]],
    min_bars: int = 20,
    forward_days: int = _FORWARD_DAYS,
) -> Dict:
    """
    Walk-forward backtest for one pattern across all tickers.
    Returns {"precision", "recall", "fpr", "fnr", "n", "n_fires", "n_positive"}.
    """
    fire_fn = _FIRE_FNS.get(category, _candlestick_fires)
    TP = FP = FN = TN = 0
    n_fires = 0
    n_positive_total = 0

    for ticker, bars in all_ticker_bars.items():
        n = len(bars)
        if n < min_bars + forward_days:
            continue
        for i in range(min_bars, n - forward_days):
            window = bars[:i + 1]
            fwd = _forward_return(bars, i, forward_days)
            if fwd is None:
                continue
            actual_pos = (fwd > 0) if direction_sign >= 0 else (fwd < 0)
            if actual_pos:
                n_positive_total += 1
            fired = fire_fn(pattern_name, window)
            if fired:
                n_fires += 1
            if fired and actual_pos:
                TP += 1
            elif fired and not actual_pos:
                FP += 1
            elif not fired and actual_pos:
                FN += 1
            else:
                TN += 1

    n = TP + FP + FN + TN
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0
    recall    = TP / (TP + FN) if (TP + FN) > 0 else 0.0
    fpr       = FP / (FP + TN) if (FP + TN) > 0 else 0.0
    fnr       = FN / (FN + TP) if (FN + TP) > 0 else 0.0
    return {
        "n": n, "n_fires": n_fires, "n_positive": n_positive_total,
        "precision": round(precision, 4), "recall": round(recall, 4),
        "fpr": round(fpr, 4), "fnr": round(fnr, 4),
        "TP": TP, "FP": FP, "FN": FN, "TN": TN,
    }


def determine_status(result: Dict, min_n: int = _MIN_N) -> str:
    n_fires = result.get("n_fires", 0)
    precision = result.get("precision", 0.0)
    if n_fires < min_n:
        return "UNTESTED"
    if precision >= _PASS_PRECISION:
        return "PASS"
    if precision <= _FAIL_PRECISION:
        return "FAIL"
    return "UNTESTED"


def run_verification(
    category_filter: Optional[str] = None,
    force: bool = False,
    max_tickers: int = 200,
    forward_days: int = _FORWARD_DAYS,
):
    """
    Run full walk-forward verification for all enabled patterns.
    Updates aiem_pattern_registry with results.
    """
    ensure_table()
    build_registry()
    registry = get_registry(enabled_only=True)

    if category_filter:
        registry = [r for r in registry if r["category"].upper() == category_filter.upper()]

    if not force:
        registry = [r for r in registry if r["status"] == "UNTESTED" or r.get("backtest_n") is None]

    if not registry:
        log.info("No patterns to verify (all tested; use --force to retest).")
        return

    log.info(f"Fetching OHLCV data (365 days)...")
    all_bars = _fetch_all_ohlcv(lookback_days=365)
    log.info(f"Loaded {len(all_bars)} tickers with data")

    if len(all_bars) > max_tickers:
        import random
        tickers = random.sample(list(all_bars.keys()), max_tickers)
        all_bars = {t: all_bars[t] for t in tickers}
        log.info(f"Sampled {max_tickers} tickers for speed")

    registry_lookup = {r["pattern_name"]: r for r in get_registry()}

    total = len(registry)
    for idx, row in enumerate(registry):
        pname = row["pattern_name"]
        cat = row["category"]
        dir_sign = _direction_sign(pname, list(registry_lookup.values()))
        log.info(f"[{idx+1}/{total}] Testing: {pname} ({cat})...")
        try:
            res = backtest_pattern(
                pname, cat, dir_sign, all_bars, forward_days=forward_days
            )
            status = determine_status(res)
            update_pattern_test_result(
                pattern_name=pname,
                status=status,
                precision_score=res["precision"],
                recall_score=res["recall"],
                false_positive_rate=res["fpr"],
                false_negative_rate=res["fnr"],
                backtest_n=res["n_fires"],
                notes=(f"TP={res['TP']} FP={res['FP']} FN={res['FN']} TN={res['TN']} "
                       f"n_fires={res['n_fires']} forward_days={forward_days}"),
            )
            log.info(f"  -> {status}: P={res['precision']:.3f} R={res['recall']:.3f} "
                     f"FPR={res['fpr']:.3f} n={res['n_fires']}")
        except Exception as e:
            log.error(f"  ERROR for {pname}: {e}")

    log.info("Verification complete.")
    from aiem_pattern_registry import print_registry_summary
    print_registry_summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIEM Pattern Registry Verifier")
    parser.add_argument("--force", action="store_true",
                        help="Re-test all patterns, not just UNTESTED ones")
    parser.add_argument("--category", type=str, default=None,
                        help="Only test patterns in this category")
    parser.add_argument("--max-tickers", type=int, default=200,
                        help="Max tickers to sample (default 200)")
    parser.add_argument("--forward-days", type=int, default=3,
                        help="Forward return window in days (default 3)")
    args = parser.parse_args()
    run_verification(
        category_filter=args.category,
        force=args.force,
        max_tickers=args.max_tickers,
        forward_days=args.forward_days,
    )
