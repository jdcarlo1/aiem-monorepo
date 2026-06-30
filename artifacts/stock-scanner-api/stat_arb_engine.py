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

def _fetch_closes(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> Optional[pd.Series]:
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # NOTE: use (%s * INTERVAL '1 day') — cannot parameterize inside INTERVAL '...'
                cur.execute("""
                    SELECT date::date AS date, close_price AS close
                    FROM polygon_market_daily
                    WHERE ticker = %s
                      AND date >= NOW() - (%s * INTERVAL '1 day')
                    ORDER BY date ASC
                """, (ticker, lookback_days + 30))
                rows = cur.fetchall()

        if not rows or len(rows) < 60:
            return None

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df["close"].dropna()

    except Exception as e:
        logger.error(f"[stat_arb] price fetch failed for {ticker}: {e}")
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
    s_a = _fetch_closes(ticker_a, lookback_days=5)
    s_b = _fetch_closes(ticker_b, lookback_days=5)

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
        return []

    logger.info(f"[stat_arb] scoring {len(db_pairs)} active pairs...")

    for row in db_pairs:
        signal = compute_current_zscore(
            ticker_a    = row["ticker_a"],
            ticker_b    = row["ticker_b"],
            hedge_ratio = row["hedge_ratio"],
            spread_mean = row["spread_mean"],
            spread_std  = row["spread_std"],
        )
        if signal.get("error"):
            continue
        log_signal(signal)
        if signal["signal_strength"] != "NONE":
            active_signals.append(signal)

    active_signals.sort(key=lambda x: abs(x.get("zscore", 0)), reverse=True)
    logger.info(f"[stat_arb] scan complete — {len(active_signals)} active signals")
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
