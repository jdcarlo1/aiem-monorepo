"""
stat_arb_engine.py
------------------
Statistical Arbitrage Engine for AIEM / StockScanner AI.

WHAT THIS DOES
--------------
Finds pairs of stocks that are cointegrated (historically move together),
monitors the current spread between them, and fires a signal when the spread
deviates beyond a z-score threshold — implying a mean-reversion trade.

This is designed as a CONVICTION BOOSTER on top of the existing 9-layer
scoring. When AIEM already has a sweep or dark-pool signal on a ticker AND
that ticker's stat-arb spread is at 2σ+, that's a materially higher-conviction
setup than either signal alone.

INTEGRATION POINTS
------------------
1. Standalone: run stat_arb_daily_scan() at market open.
2. AIEM tool: _aiem_tool_stat_arb_check(ticker) wired into _build_aiem_tool_map.
3. Scheduler: daily 9:10 AM score + Sunday 3 PM cointegration retest.
4. Endpoint: GET /stock-api/stat-arb/signals

DATA SOURCE
-----------
polygon_market_daily table (already populated at 8:35 AM daily).
No new data dependencies required.

DEPENDENCIES
------------
psycopg2, numpy, pandas, statsmodels (all already installed).
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from statsmodels.tsa.stattools import coint

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PAIRS: List[Tuple[str, str]] = [
    ("NVDA", "AMD"),
    ("NVDA", "MRVL"),
    ("AMAT", "LRCX"),
    ("AMAT", "KLAC"),
    ("MU",   "WDC"),
    ("CRDO", "MRVL"),
    ("NVDA", "AMAT"),
    ("AMD",  "INTC"),
    ("SMH",  "NVDA"),
    ("SOXX", "AMD"),
    ("META", "GOOGL"),
    ("MSFT", "GOOGL"),
    ("JPM",  "BAC"),
    ("XOM",  "CVX"),
    ("TSLA", "RIVN"),
]

ZSCORE_ENTRY   = 2.0
ZSCORE_EXIT    = 0.5
LOOKBACK_DAYS  = 252
MIN_PVALUE     = 0.05
HEDGE_WINDOW   = 60


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _connect() -> psycopg2.extensions.connection:
    url = os.environ.get("DATABASE_URL") or os.environ.get("AIEM_DATABASE_URL")
    if not url:
        raise RuntimeError("No DATABASE_URL found.")
    return psycopg2.connect(url)


DDL = """
CREATE TABLE IF NOT EXISTS stat_arb_pairs (
    id               SERIAL PRIMARY KEY,
    ticker_a         TEXT NOT NULL,
    ticker_b         TEXT NOT NULL,
    coint_pvalue     FLOAT,
    hedge_ratio      FLOAT,
    spread_mean      FLOAT,
    spread_std       FLOAT,
    last_tested      TIMESTAMPTZ DEFAULT NOW(),
    is_active        BOOLEAN DEFAULT TRUE,
    UNIQUE(ticker_a, ticker_b)
);

CREATE TABLE IF NOT EXISTS stat_arb_signals (
    id               SERIAL PRIMARY KEY,
    ticker_a         TEXT NOT NULL,
    ticker_b         TEXT NOT NULL,
    signal_date      DATE DEFAULT CURRENT_DATE,
    price_a          FLOAT,
    price_b          FLOAT,
    spread           FLOAT,
    zscore           FLOAT,
    direction        TEXT,
    signal_strength  TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stat_arb_signals_date
    ON stat_arb_signals(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_stat_arb_signals_tickers
    ON stat_arb_signals(ticker_a, ticker_b);

-- C12 remediation (2026-07-10): stat_arb_signals only ever held ACTIONABLE
-- (non-NEUTRAL) rows, so a day with zero actionable signals looked
-- byte-for-byte identical to the daily scan job never having run at all —
-- "ran, found nothing" was indistinguishable from "never executed". This
-- run-level log table records ONE row every time stat_arb_daily_scan()
-- executes, regardless of outcome, so liveness can be proven independently
-- of whether any actionable signal fired that day.
CREATE TABLE IF NOT EXISTS stat_arb_scan_log (
    id               SERIAL PRIMARY KEY,
    scan_time        TIMESTAMPTZ DEFAULT NOW(),
    pairs_evaluated  INTEGER,
    pairs_with_data  INTEGER,
    signals_found    INTEGER,
    max_abs_zscore   FLOAT,
    retest_pairs     BOOLEAN DEFAULT FALSE,
    detail_json      JSONB
);

CREATE INDEX IF NOT EXISTS idx_stat_arb_scan_log_time
    ON stat_arb_scan_log(scan_time DESC);
"""


def _init_tables() -> None:
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
            conn.commit()
        logger.info("[stat_arb] tables ready")
    except Exception as e:
        logger.error(f"[stat_arb] table init error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PRICE DATA
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_closes_tradier(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> Optional[pd.Series]:
    """
    Tradier daily-history fallback for _fetch_closes.
    Used when polygon_market_daily has insufficient history (e.g. during
    initial backfill which processes 8,626 tickers at ~250 per startup cycle).
    """
    try:
        import urllib.request as _ur, json as _json, datetime as _dt
        token = os.environ.get("TRADIER_API_TOKEN_2", "") or os.environ.get("TRADIER_API_TOKEN", "")
        if not token:
            return None
        start = (_dt.date.today() - _dt.timedelta(days=lookback_days + 30)).isoformat()
        url = (f"https://api.tradier.com/v1/markets/history"
               f"?symbol={ticker}&interval=daily&start={start}&session_filter=open")
        req = _ur.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        with _ur.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        history = (data.get("history") or {}).get("day") or []
        if not history:
            return None
        if isinstance(history, dict):
            history = [history]
        df = pd.DataFrame(history)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        if "close" not in df.columns:
            return None
        return df["close"].astype(float).dropna()
    except Exception as _e:
        logger.warning(f"[stat_arb] Tradier fallback failed for {ticker}: {_e}")
        return None


def _fetch_closes(ticker: str, lookback_days: int = LOOKBACK_DAYS,
                   min_rows: int = 60) -> Optional[pd.Series]:
    """
    C12 remediation (2026-07-10): `min_rows` used to be hardcoded to 60
    regardless of the caller's requested `lookback_days`. That's correct
    for the cointegration test (lookback_days=252 -> plenty of rows), but
    `compute_current_zscore()` calls this with lookback_days=5, which
    (even with the +30-day query pad below) can never return 60 rows —
    it was UNCONDITIONALLY returning None, meaning the live z-score signal
    generator could never produce a real signal, only "no_recent_prices"
    errors, no matter how fresh the underlying price data actually was.
    Callers that only need the latest aligned price (the z-score path)
    should pass a small min_rows (e.g. 2); the cointegration path keeps
    the default of 60.

    Data source priority:
    1. polygon_market_daily (primary — full history when backfill is complete)
    2. Tradier daily history (fallback during backfill or when polygon data is sparse)
    """
    # ── Primary: polygon_market_daily ──────────────────────────────────────
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT scan_date::date AS date, close_price AS close
                    FROM polygon_market_daily
                    WHERE ticker = %s
                      AND scan_date >= NOW() - (%s * INTERVAL '1 day')
                    ORDER BY scan_date ASC
                """, (ticker, lookback_days + 30))
                rows = cur.fetchall()

        if rows and len(rows) >= min_rows:
            df = pd.DataFrame([dict(r) for r in rows])
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return df["close"].dropna()

    except Exception as e:
        logger.error(f"[stat_arb] polygon price fetch failed for {ticker}: {e}")

    # ── Fallback: Tradier daily history ────────────────────────────────────
    series = _fetch_closes_tradier(ticker, lookback_days)
    if series is not None and len(series) >= min_rows:
        logger.info(f"[stat_arb] {ticker}: using Tradier fallback ({len(series)} rows)")
        return series

    return None


def _align_series(s_a: pd.Series, s_b: pd.Series) -> Tuple[pd.Series, pd.Series]:
    combined = pd.concat([s_a, s_b], axis=1).dropna()
    combined.columns = ["a", "b"]
    return combined["a"], combined["b"]


# ─────────────────────────────────────────────────────────────────────────────
# COINTEGRATION TEST
# ─────────────────────────────────────────────────────────────────────────────

def test_cointegration(
    ticker_a: str,
    ticker_b: str,
    lookback_days: int = LOOKBACK_DAYS,
) -> Dict[str, Any]:
    s_a = _fetch_closes(ticker_a, lookback_days)
    s_b = _fetch_closes(ticker_b, lookback_days)

    if s_a is None or s_b is None:
        return {"is_cointegrated": False, "error": "insufficient_data"}

    s_a, s_b = _align_series(s_a, s_b)

    if len(s_a) < 60:
        return {"is_cointegrated": False, "error": "insufficient_overlap"}

    try:
        score, pvalue, _ = coint(s_a.values, s_b.values)
        beta = np.polyfit(s_b.values, s_a.values, 1)[0]
        spread = s_a.values - beta * s_b.values

        spread_series = pd.Series(spread)
        roll_mean = spread_series.rolling(HEDGE_WINDOW).mean().iloc[-1]
        roll_std  = spread_series.rolling(HEDGE_WINDOW).std().iloc[-1]

        return {
            "is_cointegrated": bool(pvalue < MIN_PVALUE),
            "pvalue":          round(float(pvalue), 4),
            "hedge_ratio":     round(float(beta), 4),
            "spread_mean":     round(float(roll_mean), 4),
            "spread_std":      round(float(roll_std), 4),
            "n_observations":  len(s_a),
            "score":           round(float(score), 4),
        }
    except Exception as e:
        logger.error(f"[stat_arb] cointegration test failed {ticker_a}/{ticker_b}: {e}")
        return {"is_cointegrated": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# SPREAD Z-SCORE
# ─────────────────────────────────────────────────────────────────────────────

def compute_current_zscore(
    ticker_a: str,
    ticker_b: str,
    hedge_ratio: float,
    spread_mean: float,
    spread_std: float,
) -> Dict[str, Any]:
    s_a = _fetch_closes(ticker_a, lookback_days=5, min_rows=2)
    s_b = _fetch_closes(ticker_b, lookback_days=5, min_rows=2)

    if s_a is None or s_b is None or len(s_a) == 0 or len(s_b) == 0:
        return {"error": "no_recent_prices"}

    s_a, s_b = _align_series(s_a, s_b)
    if len(s_a) == 0:
        return {"error": "no_aligned_prices"}

    price_a = float(s_a.iloc[-1])
    price_b = float(s_b.iloc[-1])
    spread  = price_a - hedge_ratio * price_b

    if spread_std == 0:
        return {"error": "zero_spread_std"}

    zscore = (spread - spread_mean) / spread_std

    if abs(zscore) >= 2.5:
        strength = "STRONG"
    elif abs(zscore) >= ZSCORE_ENTRY:
        strength = "MODERATE"
    else:
        strength = "NONE"

    if zscore > ZSCORE_ENTRY:
        direction = "SHORT_A_LONG_B"
    elif zscore < -ZSCORE_ENTRY:
        direction = "LONG_A_SHORT_B"
    else:
        direction = "NEUTRAL"

    return {
        "ticker_a":        ticker_a,
        "ticker_b":        ticker_b,
        "price_a":         round(price_a, 2),
        "price_b":         round(price_b, 2),
        "spread":          round(spread, 4),
        "spread_mean":     round(spread_mean, 4),
        "spread_std":      round(spread_std, 4),
        "zscore":          round(float(zscore), 3),
        "direction":       direction,
        "signal_strength": strength,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PAIR REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

def register_pair(ticker_a: str, ticker_b: str, test_result: Dict[str, Any]) -> None:
    if not test_result.get("is_cointegrated"):
        return
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO stat_arb_pairs
                        (ticker_a, ticker_b, coint_pvalue, hedge_ratio,
                         spread_mean, spread_std, last_tested, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), TRUE)
                    ON CONFLICT (ticker_a, ticker_b) DO UPDATE SET
                        coint_pvalue = EXCLUDED.coint_pvalue,
                        hedge_ratio  = EXCLUDED.hedge_ratio,
                        spread_mean  = EXCLUDED.spread_mean,
                        spread_std   = EXCLUDED.spread_std,
                        last_tested  = NOW(),
                        is_active    = TRUE
                """, (
                    ticker_a, ticker_b,
                    test_result["pvalue"],
                    test_result["hedge_ratio"],
                    test_result["spread_mean"],
                    test_result["spread_std"],
                ))
            conn.commit()
        logger.info(f"[stat_arb] registered {ticker_a}/{ticker_b} p={test_result['pvalue']}")
    except Exception as e:
        logger.error(f"[stat_arb] register_pair error: {e}")


def log_signal(signal: Dict[str, Any]) -> None:
    if signal.get("error") or signal.get("direction") == "NEUTRAL":
        return
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO stat_arb_signals
                        (ticker_a, ticker_b, signal_date, price_a, price_b,
                         spread, zscore, direction, signal_strength)
                    VALUES (%s, %s, CURRENT_DATE, %s, %s, %s, %s, %s, %s)
                """, (
                    signal["ticker_a"], signal["ticker_b"],
                    signal["price_a"],  signal["price_b"],
                    signal["spread"],   signal["zscore"],
                    signal["direction"], signal["signal_strength"],
                ))
            conn.commit()
    except Exception as e:
        logger.error(f"[stat_arb] log_signal error: {e}")


def _log_scan_run(pairs_evaluated: int, pairs_with_data: int,
                   signals_found: int, max_abs_zscore: Optional[float],
                   retest_pairs: bool, detail: List[Dict[str, Any]]) -> None:
    """
    C12 remediation: record ONE row per stat_arb_daily_scan() execution,
    unconditionally — even when 0 active pairs exist or 0 signals fire.
    This is what makes "the job ran today and legitimately found nothing"
    provable and distinguishable from "the scheduler job silently died".
    """
    import json as _json
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO stat_arb_scan_log
                        (pairs_evaluated, pairs_with_data, signals_found,
                         max_abs_zscore, retest_pairs, detail_json)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    pairs_evaluated, pairs_with_data, signals_found,
                    max_abs_zscore, retest_pairs, _json.dumps(detail),
                ))
            conn.commit()
    except Exception as e:
        logger.error(f"[stat_arb] _log_scan_run error: {e}")


def get_last_scan_log(limit: int = 20) -> List[Dict[str, Any]]:
    """C12: expose the run-level audit trail (liveness proof) for the dashboard/admin."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, scan_time, pairs_evaluated, pairs_with_data,
                           signals_found, max_abs_zscore, retest_pairs, detail_json
                    FROM stat_arb_scan_log
                    ORDER BY scan_time DESC
                    LIMIT %s
                """, (limit,))
                rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if hasattr(d.get("scan_time"), "isoformat"):
                d["scan_time"] = d["scan_time"].isoformat()
            out.append(d)
        return out
    except Exception as e:
        logger.error(f"[stat_arb] get_last_scan_log error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# DAILY SCAN
# ─────────────────────────────────────────────────────────────────────────────

def stat_arb_daily_scan(
    pairs: List[Tuple[str, str]] = DEFAULT_PAIRS,
    retest_pairs: bool = False,
) -> List[Dict[str, Any]]:
    """
    Full daily stat-arb scan.
    retest_pairs=True re-runs cointegration tests (slow; do weekly not daily).
    Returns list of active signals sorted by |z-score| descending.
    """
    _init_tables()
    active_signals = []

    if retest_pairs:
        logger.info(f"[stat_arb] running cointegration tests on {len(pairs)} pairs...")
        for ticker_a, ticker_b in pairs:
            result = test_cointegration(ticker_a, ticker_b)
            if result.get("is_cointegrated"):
                register_pair(ticker_a, ticker_b, result)
                logger.info(f"[stat_arb] ✓ {ticker_a}/{ticker_b} p={result['pvalue']} β={result['hedge_ratio']}")
            else:
                logger.info(f"[stat_arb] ✗ {ticker_a}/{ticker_b} p={result.get('pvalue', 'N/A')}")

    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT ticker_a, ticker_b, hedge_ratio, spread_mean, spread_std
                    FROM stat_arb_pairs
                    WHERE is_active = TRUE
                """)
                db_pairs = cur.fetchall()
    except Exception as e:
        logger.error(f"[stat_arb] load pairs error: {e}")
        db_pairs = []

    if not db_pairs:
        logger.warning("[stat_arb] no active pairs in DB — run with retest_pairs=True first")
        _log_scan_run(pairs_evaluated=0, pairs_with_data=0, signals_found=0,
                      max_abs_zscore=None, retest_pairs=retest_pairs, detail=[])
        return []

    logger.info(f"[stat_arb] scoring {len(db_pairs)} active pairs...")

    detail: List[Dict[str, Any]] = []
    pairs_with_data = 0
    for row in db_pairs:
        signal = compute_current_zscore(
            ticker_a    = row["ticker_a"],
            ticker_b    = row["ticker_b"],
            hedge_ratio = row["hedge_ratio"],
            spread_mean = row["spread_mean"],
            spread_std  = row["spread_std"],
        )
        if signal.get("error"):
            detail.append({"ticker_a": row["ticker_a"], "ticker_b": row["ticker_b"],
                            "error": signal["error"]})
            continue
        pairs_with_data += 1
        log_signal(signal)
        detail.append({"ticker_a": signal["ticker_a"], "ticker_b": signal["ticker_b"],
                        "zscore": signal["zscore"], "direction": signal["direction"],
                        "signal_strength": signal["signal_strength"]})
        if signal["signal_strength"] != "NONE":
            active_signals.append(signal)

    active_signals.sort(key=lambda x: abs(x.get("zscore", 0)), reverse=True)
    max_abs_z = max((abs(d["zscore"]) for d in detail if "zscore" in d), default=None)
    _log_scan_run(pairs_evaluated=len(db_pairs), pairs_with_data=pairs_with_data,
                  signals_found=len(active_signals), max_abs_zscore=max_abs_z,
                  retest_pairs=retest_pairs, detail=detail)
    logger.info(f"[stat_arb] scan complete — {len(active_signals)} active signals "
                f"(run logged: {pairs_with_data}/{len(db_pairs)} pairs had data)")
    return active_signals


# ─────────────────────────────────────────────────────────────────────────────
# AIEM TOOL
# ─────────────────────────────────────────────────────────────────────────────

def _aiem_tool_stat_arb_check(ticker: str) -> Dict[str, Any]:
    """
    AIEM tool: given a ticker AIEM is analyzing, find any active stat-arb
    signals involving that ticker. Use as a conviction booster when sweep or
    dark-pool signals coincide with a 2σ+ spread divergence.
    """
    _init_tables()

    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT ticker_a, ticker_b, hedge_ratio, spread_mean, spread_std
                    FROM stat_arb_pairs
                    WHERE is_active = TRUE
                      AND (ticker_a = %s OR ticker_b = %s)
                """, (ticker, ticker))
                rows = cur.fetchall()
    except Exception as e:
        return {"ticker": ticker, "pairs_found": 0, "signals": [], "top_zscore": 0.0,
                "conviction_boost": "NONE", "summary": f"DB error: {e}"}

    if not rows:
        return {
            "ticker":           ticker,
            "pairs_found":      0,
            "signals":          [],
            "top_zscore":       0.0,
            "conviction_boost": "NONE",
            "summary":          f"No active stat-arb pairs found for {ticker}.",
        }

    signals = []
    for row in rows:
        sig = compute_current_zscore(
            ticker_a    = row["ticker_a"],
            ticker_b    = row["ticker_b"],
            hedge_ratio = row["hedge_ratio"],
            spread_mean = row["spread_mean"],
            spread_std  = row["spread_std"],
        )
        if not sig.get("error"):
            signals.append(sig)

    signals.sort(key=lambda x: abs(x.get("zscore", 0)), reverse=True)
    top_z = max((abs(s.get("zscore", 0)) for s in signals), default=0.0)

    if top_z >= 2.5:
        boost = "HIGH"
    elif top_z >= ZSCORE_ENTRY:
        boost = "MODERATE"
    else:
        boost = "NONE"

    if signals:
        top = signals[0]
        direction_str = (
            f"LONG {top['ticker_a']} / SHORT {top['ticker_b']}"
            if top["direction"] == "LONG_A_SHORT_B"
            else f"SHORT {top['ticker_a']} / LONG {top['ticker_b']}"
            if top["direction"] == "SHORT_A_LONG_B"
            else "NEUTRAL"
        )
        summary = (
            f"{ticker} stat-arb: top pair {top['ticker_a']}/{top['ticker_b']} "
            f"z={top['zscore']:.2f} ({top['signal_strength']}) → {direction_str}. "
            f"Conviction boost: {boost}."
        )
    else:
        summary = f"{ticker}: stat-arb pairs found but no current spread data."

    return {
        "ticker":           ticker,
        "pairs_found":      len(rows),
        "signals":          signals,
        "top_zscore":       round(top_z, 3),
        "conviction_boost": boost,
        "summary":          summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RECENT SIGNALS QUERY
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_signals(days_back: int = 5) -> pd.DataFrame:
    """Return recent stat-arb signals for the dashboard endpoint."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT ticker_a, ticker_b, signal_date,
                           price_a, price_b, spread, zscore,
                           direction, signal_strength, created_at
                    FROM stat_arb_signals
                    WHERE signal_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                    ORDER BY signal_date DESC, ABS(zscore) DESC
                """, (days_back,))
                rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as e:
        logger.error(f"[stat_arb] get_recent_signals error: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN  (manual trigger / smoke test)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    retest = "--retest" in sys.argv
    print("=" * 60)
    print("STAT ARB ENGINE — Daily Scan")
    print(f"Mode: {'RETEST PAIRS' if retest else 'SCORE ONLY'}")
    print("=" * 60)

    signals = stat_arb_daily_scan(retest_pairs=retest)

    if not signals:
        print("No active signals today.")
    else:
        print(f"\n{'PAIR':<20} {'Z-SCORE':>8} {'STRENGTH':<10} {'DIRECTION'}")
        print("-" * 70)
        for s in signals:
            pair = f"{s['ticker_a']}/{s['ticker_b']}"
            print(f"{pair:<20} {s['zscore']:>+8.2f} {s['signal_strength']:<10} {s['direction']}")

    print("\n--- AIEM Tool Test: NVDA ---")
    result = _aiem_tool_stat_arb_check("NVDA")
    print(result["summary"])
