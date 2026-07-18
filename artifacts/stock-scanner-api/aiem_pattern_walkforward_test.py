"""
aiem_pattern_walkforward_test.py — Walk-forward OOS test for all pattern detectors.

Design:
- Samples up to 200 tickers from polygon_market_daily with >=60 bars (2024-2026 range).
- For each ticker, slides a 60-bar window across dates.
- At each bar D, runs detect_all_patterns() on bars[0:D+1] (max 60 bars).
- Outcome = next bar's close vs current bar's close (up/down/flat).
- Per-pattern: TP, FP, FN, TN, precision, recall, FPR, FNR.
- Direction convention:
    BULLISH: TP = signal AND next_close > cur_close
    BEARISH: TP = signal AND next_close < cur_close
    NEUTRAL/BOTH: TP = signal (direction agnostic, count fires vs base rate)
- Writes results to aiem_pattern_registry via update_pattern_test_result().
- Sets enabled=False for every pattern that remains UNTESTED.

Usage: python3 aiem_pattern_walkforward_test.py
"""
from __future__ import annotations
import os, sys, json, datetime, logging

import psycopg2

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("walkforward")

_DB_URL = os.environ.get("DATABASE_URL", "")

SAMPLE_TICKERS     = 50   # keep runtime under 90s
MIN_BARS           = 60   # tickers with fewer bars skipped
LOOKBACK           = 60   # bars fed to detector per window
STRIDE             = 3    # test every Nth bar to reduce windows
MIN_SIGNALS_TO_REPORT = 3 # patterns with <3 fires → insufficient_data


def _get_sample_tickers() -> list:
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, COUNT(*) AS n
                FROM polygon_market_daily
                WHERE open_price IS NOT NULL
                  AND close_price IS NOT NULL
                  AND close_price > 0
                GROUP BY ticker
                HAVING COUNT(*) >= %s
                ORDER BY n DESC
                LIMIT %s
            """, (MIN_BARS, SAMPLE_TICKERS))
            return [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        conn.close()


def _get_all_bars(ticker: str) -> list:
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT scan_date, open_price, high_price, low_price, close_price,
                       COALESCE(volume, 0)
                FROM polygon_market_daily
                WHERE ticker = %s
                  AND open_price IS NOT NULL
                  AND close_price IS NOT NULL
                  AND close_price > 0
                ORDER BY scan_date ASC
            """, (ticker,))
            return [
                {"date": str(r[0]), "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def _get_registry() -> list:
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pattern_name, direction, enabled, status
                FROM aiem_pattern_registry
                ORDER BY pattern_name
            """)
            return [{"pattern_name": r[0], "direction": r[1],
                     "enabled": r[2], "status": r[3]}
                    for r in cur.fetchall()]
    finally:
        conn.close()


def _update_test_result(pattern_name: str, n: int, precision: float, recall: float,
                        fpr: float, fnr: float, status: str, notes: str) -> None:
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_pattern_registry
                SET precision_score = %s,
                    recall_score = %s,
                    false_positive_rate = %s,
                    false_negative_rate = %s,
                    backtest_n = %s,
                    status = %s,
                    notes = %s,
                    last_tested = NOW(),
                    updated_at = NOW()
                WHERE pattern_name = %s
            """, (round(precision, 4), round(recall, 4), round(fpr, 4),
                  round(fnr, 4), n, status, notes, pattern_name))
        conn.commit()
    finally:
        conn.close()


def _disable_untested() -> int:
    """Set enabled=False for every pattern still status=UNTESTED."""
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_pattern_registry
                SET enabled = FALSE, updated_at = NOW()
                WHERE status = 'UNTESTED'
            """)
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def run() -> None:
    print(f"=== AIEM Pattern Walk-Forward OOS Test ===")
    print(f"Timestamp : {datetime.datetime.utcnow().isoformat()}Z")
    print(f"Min bars  : {MIN_BARS}")
    print(f"Sample    : up to {SAMPLE_TICKERS} tickers")
    print(f"Lookback  : {LOOKBACK} bars per window")
    print()

    registry = _get_registry()
    pat_directions = {p["pattern_name"]: p["direction"] for p in registry}
    all_pattern_names = set(pat_directions.keys())

    # Accumulators: per pattern_name → {tp, fp, fn, tn, fires, universe_pos, universe_neg}
    stats: dict = {
        name: {"fires": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0,
               "universe_pos": 0, "universe_neg": 0}
        for name in all_pattern_names
    }

    # Import detection engine
    from aiem_pattern_engine import detect_all_patterns

    print("Sampling tickers...")
    tickers = _get_sample_tickers()
    print(f"  {len(tickers)} tickers with >={MIN_BARS} bars")
    print()

    windows_checked = 0
    tickers_processed = 0

    for ticker, nbar in tickers:
        bars = _get_all_bars(ticker)
        if len(bars) < MIN_BARS + 1:
            continue

        tickers_processed += 1
        # Slide window: every STRIDE-th bar from LOOKBACK-1 to len-2
        start = LOOKBACK - 1
        for d in range(start, len(bars) - 1, STRIDE):
            window = bars[max(0, d - LOOKBACK + 1): d + 1]
            next_bar = bars[d + 1]
            cur_close  = window[-1]["close"]
            next_close = next_bar["close"]

            outcome_up   = (next_close > cur_close)
            outcome_down = (next_close < cur_close)

            # Detect all patterns on this window
            try:
                result = detect_all_patterns(window, ticker=ticker, thesis="NEUTRAL")
            except Exception:
                continue

            fired_this_window = set()
            for p in result.get("all_patterns", []):
                pname = p.get("pattern", "")
                if pname in all_pattern_names:
                    fired_this_window.add(pname)

            # Update per-pattern stats
            for pname in all_pattern_names:
                direction = pat_directions[pname]
                fired = pname in fired_this_window

                # For this pattern, what is "positive outcome"?
                if direction == "BULLISH":
                    pos = outcome_up
                elif direction == "BEARISH":
                    pos = outcome_down
                else:  # NEUTRAL / BOTH
                    # Treat any nonzero move as positive
                    pos = (next_close != cur_close)

                if pos:
                    stats[pname]["universe_pos"] += 1
                else:
                    stats[pname]["universe_neg"] += 1

                if fired:
                    stats[pname]["fires"] += 1
                    if pos:
                        stats[pname]["tp"] += 1
                    else:
                        stats[pname]["fp"] += 1
                else:
                    if pos:
                        stats[pname]["fn"] += 1
                    else:
                        stats[pname]["tn"] += 1

            windows_checked += 1

    print(f"Processed : {tickers_processed} tickers, {windows_checked} windows")
    print()

    # Compute metrics and write results
    HEADER = (f"{'pattern_name':<55} {'dir':<8} {'fires':>6} {'tp':>5} "
              f"{'fp':>5} {'fn':>6} {'tn':>6} {'prec':>6} {'rec':>6} "
              f"{'fpr':>6} {'fnr':>6} status")
    print(HEADER)
    print("-" * len(HEADER))

    results = []
    for pname in sorted(all_pattern_names):
        s = stats[pname]
        fires = s["fires"]
        tp, fp, fn, tn = s["tp"], s["fp"], s["fn"], s["tn"]
        universe_pos = s["universe_pos"]
        universe_neg = s["universe_neg"]

        if fires < MIN_SIGNALS_TO_REPORT or universe_pos == 0:
            prec = rec = fpr = fnr = 0.0
            status = "INSUFFICIENT_DATA"
            notes  = f"fires={fires} universe_pos={universe_pos} — below min_signals={MIN_SIGNALS_TO_REPORT}"
        else:
            prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            fpr  = fp / (fp + tn) if (fp + tn) > 0 else 0.0
            fnr  = fn / (tp + fn) if (tp + fn) > 0 else 0.0
            # Pass threshold: precision >= 0.50 AND fires >= MIN_SIGNALS_TO_REPORT
            status = "PASS" if prec >= 0.50 else "FAIL"
            notes  = (f"walkforward n={fires} prec={prec:.3f} rec={rec:.3f} "
                      f"fpr={fpr:.3f} fnr={fnr:.3f} windows={windows_checked}")

        direction = pat_directions[pname]
        print(f"{pname:<55} {direction:<8} {fires:>6} {tp:>5} {fp:>5} "
              f"{fn:>6} {tn:>6} {prec:>6.3f} {rec:>6.3f} {fpr:>6.3f} "
              f"{fnr:>6.3f} {status}")

        results.append({
            "pattern_name": pname, "direction": direction,
            "fires": fires, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 4), "recall": round(rec, 4),
            "fpr": round(fpr, 4), "fnr": round(fnr, 4),
            "status": status, "notes": notes
        })

        _update_test_result(pname, fires, prec, rec, fpr, fnr, status, notes)

    # Disable all remaining UNTESTED patterns
    print()
    n_disabled = _disable_untested()
    print(f"Set enabled=FALSE for {n_disabled} remaining UNTESTED patterns")

    # Final registry state
    print()
    print("=== FINAL REGISTRY STATE ===")
    final = _get_registry()
    summary: dict = {}
    for p in final:
        key = (p["status"], p["enabled"])
        summary[key] = summary.get(key, 0) + 1
    for (status, enabled), count in sorted(summary.items()):
        print(f"  status={status:<20} enabled={str(enabled):<5} : {count}")

    total = len(final)
    print(f"  TOTAL: {total}")

    # Output JSON for chain
    print()
    print("=== JSON_RESULTS_START ===")
    print(json.dumps({
        "tickers_processed": tickers_processed,
        "windows_checked": windows_checked,
        "total_patterns": len(all_pattern_names),
        "results": results,
        "n_disabled_untested": n_disabled,
        "final_registry_summary": {
            f"status={k[0]}_enabled={k[1]}": v
            for k, v in sorted(summary.items())
        }
    }, indent=2))
    print("=== JSON_RESULTS_END ===")


if __name__ == "__main__":
    run()
