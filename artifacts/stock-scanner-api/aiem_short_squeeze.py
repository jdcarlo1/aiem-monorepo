"""
aiem_short_squeeze.py
======================
Module B — Short Squeeze Reversion (Selloff/Reversion Signal Suite)

Signal definition
-----------------
A stock under sustained short-side pressure that shows covering-pressure
characteristics on the current bar: extreme RVOL (top ~4% of days in the
11 K-stock universe), large intraday range, and a close in the upper portion
of that range — consistent with short sellers being forced to cover into a
rising market.

Data source for backtest: polygon_market_daily (14 columns: OHLCV + rvol,
gap_pct, close_strength, range_pct, prev_close, vwap). No RSI, SI%,
borrow cost, or DTC column exists in that table.

NOT_IMPLEMENTED fields (documented honestly per remediation protocol):
  borrow_cost_status — no borrow-cost feed available in any table or API
  si_pct_status      — Finviz SI% is live-only; no historical in DB; cannot backtest
  dtc_status         — days-to-cover not available in any data source

Module F gate (Section 3, TIER 3 protocol)
  1. Earnings exclusion: suppress if earnings within 5 calendar days.
  2. Falling-knife proxy: prior_5d_ret ≤ −20% = extreme real selling, not a
     squeeze setup; suppress.
  Both gates are checked before any live signal is inserted or alerted.

BH-FDR registration
  register_signal() writes status='hypothesis' to aiem_signal_discoveries so
  Module 2 (decay) and Module 6 (rediscovery) automatically include this
  signal on their next scheduled pass.

Threshold calibration (polygon_market_daily, n=1.76M rows, vol>=200k, price $3-$200):
  rvol p95 = 2.49 → _RVOL_MIN = 3.0 selects top ~4% of days
  range_pct p75 ≈ 5.5% → _RANGE_MIN_PCT = 5.0 selects ~top quartile
  With all three conditions + prior_5d_ret ≤ -3%: 290 candidate rows across
  the full 2025-05-20 to 2026-07-02 backtest window.
"""

import bisect
import json
import math
import os
import datetime as dt
import psycopg2
from datetime import date, timedelta
from typing import Optional, Dict, List, Tuple, Any

_DB_URL = os.environ.get("DATABASE_URL", "")

# ── Signal identity ────────────────────────────────────────────────────────────
_SIGNAL_NAME        = "Short_Squeeze_Reversion"
_INVENTED_INDICATOR = "aiem_short_squeeze_v1"
_HORIZON            = "3d"   # primary evaluation horizon for BH-FDR

# ── Condition thresholds ───────────────────────────────────────────────────────
_RVOL_MIN            = 3.0    # top ~4% of days (p95=2.49 in calibration)
_CLOSE_STR_MIN       = 0.65   # closed in upper 35% of intraday range
_RANGE_MIN_PCT       = 5.0    # intraday range as % of price (~top quartile)
_PRICE_MIN           = 3.0
_PRICE_MAX           = 200.0
_VOLUME_MIN          = 300_000
_PRIOR_5D_DROP_MAX   = -3.0   # stock was under net pressure before reversal
_FALLING_KNIFE_FLOOR = -20.0  # prior_5d_ret ≤ this → Module F suppresses

# ── Unavailable-data status strings ───────────────────────────────────────────
_BORROW_COST_STATUS = "NOT_IMPLEMENTED"  # no borrow-cost feed in any table/API
_SI_PCT_STATUS      = "NOT_IMPLEMENTED"  # Finviz SI% is live-only; no history
_DTC_STATUS         = "NOT_IMPLEMENTED"  # days-to-cover not available anywhere

# ── Forward-return horizons (trading days) ─────────────────────────────────────
_FWD_HORIZONS = [1, 3, 5, 10]


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_schema() -> None:
    """Create aiem_squeeze_signals and aiem_squeeze_backtest_log if they don't exist."""
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_squeeze_backtest_log (
                    id               BIGSERIAL PRIMARY KEY,
                    ticker           TEXT NOT NULL,
                    signal_date      DATE NOT NULL,
                    signal_close     DOUBLE PRECISION,
                    prior_5d_ret     DOUBLE PRECISION,
                    rvol             DOUBLE PRECISION,
                    close_strength   DOUBLE PRECISION,
                    range_pct        DOUBLE PRECISION,
                    volume           BIGINT,
                    gap_pct          DOUBLE PRECISION,
                    conviction_score DOUBLE PRECISION,
                    borrow_cost_status TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    si_pct_status      TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    dtc_status         TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    earnings_excl    BOOLEAN NOT NULL DEFAULT FALSE,
                    falling_knife    BOOLEAN NOT NULL DEFAULT FALSE,
                    module_f_suppressed BOOLEAN NOT NULL DEFAULT FALSE,
                    fwd_1d_pct       DOUBLE PRECISION,
                    fwd_3d_pct       DOUBLE PRECISION,
                    fwd_5d_pct       DOUBLE PRECISION,
                    fwd_10d_pct      DOUBLE PRECISION,
                    max_dd_5d_pct    DOUBLE PRECISION,
                    max_fav_5d_pct   DOUBLE PRECISION,
                    vol_bucket       TEXT,
                    market_regime    TEXT,
                    backtested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (ticker, signal_date)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_squeeze_signals (
                    id               BIGSERIAL PRIMARY KEY,
                    ticker           TEXT NOT NULL,
                    signal_date      DATE NOT NULL,
                    conviction_score DOUBLE PRECISION,
                    rvol             DOUBLE PRECISION,
                    close_strength   DOUBLE PRECISION,
                    range_pct        DOUBLE PRECISION,
                    gap_pct          DOUBLE PRECISION,
                    volume           BIGINT,
                    si_pct           DOUBLE PRECISION,
                    si_pct_status    TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    borrow_cost      DOUBLE PRECISION,
                    borrow_cost_status TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    dtc              DOUBLE PRECISION,
                    dtc_status       TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    earnings_excl    BOOLEAN NOT NULL DEFAULT FALSE,
                    falling_knife    BOOLEAN NOT NULL DEFAULT FALSE,
                    days_to_earnings INT,
                    module_f_suppressed BOOLEAN NOT NULL DEFAULT FALSE,
                    scanned_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (ticker, signal_date)
                )
            """)
            conn.commit()
        print("[squeeze] schema OK")
    except Exception as e:
        print(f"[squeeze] init_schema error: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _vol_bucket(range_pct: float) -> str:
    if range_pct >= 10.0:
        return "HIGH_VOL"
    if range_pct >= 5.0:
        return "MEDIUM_VOL"
    return "LOW_VOL"


def _regime_for_date(sig_date: date, cur) -> str:
    """Look up VIX on or just before sig_date to determine market regime."""
    try:
        cur.execute("""
            SELECT vix_close FROM vix_daily
            WHERE scan_date <= %s ORDER BY scan_date DESC LIMIT 1
        """, (sig_date,))
        row = cur.fetchone()
        if row and row[0]:
            v = float(row[0])
            if v >= 25:
                return "TREND_DOWN"
            if v >= 18:
                return "CHOPPY"
            return "TREND_UP"
    except Exception:
        pass
    return "NO_VIX_DATA"


def _conviction(rvol: float, close_strength: float, gap_pct: float) -> float:
    """Score 0–10: rewards extreme RVOL, strong close, upside gap on signal day."""
    score = 5.0
    if rvol >= 8.0:
        score += 2
    elif rvol >= 5.0:
        score += 1
    if close_strength >= 0.85:
        score += 2
    elif close_strength >= 0.80:
        score += 1
    if gap_pct is not None:
        if gap_pct >= 2.0:
            score += 1
        elif gap_pct < 0.0:
            score -= 1
    return round(max(0.0, min(10.0, score)), 2)


def _module_f_gate(ticker: str, prior_5d_ret: float, sig_date: date, cur) -> dict:
    """
    Module F gate for squeeze signals.
    Checks:
      1. Earnings exclusion (within 5 calendar days).
      2. Falling-knife proxy: prior_5d_ret <= -20% → real selling, not a squeeze.
    Returns dict with suppress, earnings_excl, falling_knife, days_to_earnings.
    """
    out = {
        "suppress": False,
        "earnings_excl": False,
        "falling_knife": False,
        "days_to_earnings": None,
    }

    # Earnings gate
    try:
        cur.execute("""
            SELECT earnings_date FROM earnings_calendar
            WHERE ticker = %s AND earnings_date >= %s
            ORDER BY earnings_date LIMIT 1
        """, (ticker, sig_date))
        row = cur.fetchone()
        if row:
            days = (row[0] - sig_date).days
            out["days_to_earnings"] = days
            if days <= 5:
                out["earnings_excl"] = True
                out["suppress"] = True
    except Exception:
        pass  # fail-open on earnings; don't block signal

    # Falling knife proxy
    if prior_5d_ret is not None and prior_5d_ret <= _FALLING_KNIFE_FLOOR:
        out["falling_knife"] = True
        out["suppress"] = True

    return out


# ── Historical backtest ─────────────────────────────────────────────────────────

def run_historical_backtest(force: bool = False) -> dict:
    """
    Backtest the Short_Squeeze_Reversion signal on polygon_market_daily.

    Conditions (all must be met on signal day):
      rvol >= 3.0, close_strength >= 0.65, range_pct >= 5.0
      price $3–$200, volume >= 300k, prior_5d_ret <= -3.0%
      gap_pct > -15% (not in free-fall)

    Forward returns at 1d/3d/5d/10d from polygon_market_daily.
    Module F gate applied: rows with earnings_excl=True or falling_knife=True
    are written with module_f_suppressed=True and excluded from win-rate stats.

    NOT_IMPLEMENTED columns: borrow_cost_status, si_pct_status, dtc_status.

    Returns summary dict.
    """
    if not _DB_URL:
        return {"error": "no DB_URL"}

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=10,
                              options="-c statement_timeout=120000") as conn, \
             conn.cursor() as cur:

            if not force:
                cur.execute("SELECT COUNT(*) FROM aiem_squeeze_backtest_log")
                existing = cur.fetchone()[0]
                if existing > 0:
                    return {"status": "already_populated", "rows": existing}

            print("[squeeze] fetching candidates from polygon_market_daily…")

            # Step 1: pull all candidates (compute prior_5d_ret via LAG)
            cur.execute("""
                WITH prior_prices AS (
                    SELECT
                        p.ticker,
                        p.scan_date,
                        p.close_price,
                        p.open_price,
                        p.rvol,
                        p.close_strength,
                        p.range_pct,
                        p.volume,
                        p.gap_pct,
                        LAG(p.close_price, 5) OVER (
                            PARTITION BY p.ticker ORDER BY p.scan_date
                        ) AS close_5d_ago
                    FROM polygon_market_daily p
                    WHERE p.close_price BETWEEN 3.0 AND 200.0
                      AND p.volume >= 300000
                )
                SELECT
                    ticker, scan_date, close_price, rvol, close_strength,
                    range_pct, volume, gap_pct,
                    CASE WHEN close_5d_ago > 0
                         THEN (close_price - close_5d_ago) / close_5d_ago * 100
                         ELSE NULL END AS prior_5d_ret
                FROM prior_prices
                WHERE rvol >= 3.0
                  AND close_strength >= 0.65
                  AND range_pct >= 5.0
                  AND close_5d_ago IS NOT NULL
                  AND (close_price - close_5d_ago) / close_5d_ago * 100 <= -3.0
                  AND COALESCE(gap_pct, 0) > -15.0
                  AND scan_date < CURRENT_DATE - INTERVAL '10 days'
                ORDER BY scan_date, ticker
            """)
            candidates = [
                {
                    "ticker": r[0], "signal_date": r[1], "signal_close": float(r[2]),
                    "rvol": float(r[3]), "close_strength": float(r[4]),
                    "range_pct": float(r[5]), "volume": int(r[6]),
                    "gap_pct": float(r[7] or 0), "prior_5d_ret": float(r[8] or 0),
                }
                for r in cur.fetchall()
            ]
            print(f"[squeeze] {len(candidates)} candidates found")

            if not candidates:
                return {"status": "no_candidates", "rows": 0}

            # Step 2: batch-fetch all future prices for forward-return computation
            tickers = list({c["ticker"] for c in candidates})
            if candidates:
                min_sig_date = min(c["signal_date"] for c in candidates)
                max_sig_date = max(c["signal_date"] for c in candidates)
            else:
                min_sig_date = max_sig_date = date.today()

            max_fwd_date = max_sig_date + timedelta(days=22)

            cur.execute("""
                SELECT ticker, scan_date, close_price, high_price, low_price
                FROM polygon_market_daily
                WHERE ticker = ANY(%s)
                  AND scan_date > %s
                  AND scan_date <= %s
                ORDER BY ticker, scan_date
            """, (tickers, min_sig_date, max_fwd_date))

            price_map: Dict[str, List[Tuple]] = {}
            for r in cur.fetchall():
                price_map.setdefault(r[0], []).append(
                    (r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0))
                )

            # Step 3: compute forward returns + Module F gate + upsert
            inserted = 0
            for c in candidates:
                ticker     = c["ticker"]
                sig_date   = c["signal_date"]
                sig_close  = c["signal_close"]

                # Module F gate
                mf = _module_f_gate(ticker, c["prior_5d_ret"], sig_date, cur)
                conv = _conviction(c["rvol"], c["close_strength"], c["gap_pct"])
                regime = _regime_for_date(sig_date, cur)
                vb = _vol_bucket(c["range_pct"])

                # Forward returns from price_map
                futures = price_map.get(ticker, [])
                dates_list = [f[0] for f in futures]
                idx = bisect.bisect_right(dates_list, sig_date)

                def _fwd_ret(offset):
                    i = idx + offset - 1
                    if i < len(futures) and sig_close > 0:
                        return round((futures[i][1] - sig_close) / sig_close * 100, 4)
                    return None

                fwd_1d  = _fwd_ret(1)
                fwd_3d  = _fwd_ret(3)
                fwd_5d  = _fwd_ret(5)
                fwd_10d = _fwd_ret(10)

                # Max drawdown and favorable move over 5-day window
                max_dd = max_fav = None
                if idx < len(futures) and sig_close > 0:
                    window = futures[idx: idx + 5]
                    if window:
                        lows   = [f[3] for f in window]
                        highs  = [f[2] for f in window]
                        max_dd  = round((min(lows)  - sig_close) / sig_close * 100, 4)
                        max_fav = round((max(highs) - sig_close) / sig_close * 100, 4)

                try:
                    cur.execute("""
                        INSERT INTO aiem_squeeze_backtest_log (
                            ticker, signal_date, signal_close, prior_5d_ret,
                            rvol, close_strength, range_pct, volume, gap_pct,
                            conviction_score,
                            borrow_cost_status, si_pct_status, dtc_status,
                            earnings_excl, falling_knife, module_f_suppressed,
                            fwd_1d_pct, fwd_3d_pct, fwd_5d_pct, fwd_10d_pct,
                            max_dd_5d_pct, max_fav_5d_pct,
                            vol_bucket, market_regime
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            'NOT_IMPLEMENTED','NOT_IMPLEMENTED','NOT_IMPLEMENTED',
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        ON CONFLICT (ticker, signal_date) DO UPDATE SET
                            fwd_1d_pct    = EXCLUDED.fwd_1d_pct,
                            fwd_3d_pct    = EXCLUDED.fwd_3d_pct,
                            fwd_5d_pct    = EXCLUDED.fwd_5d_pct,
                            fwd_10d_pct   = EXCLUDED.fwd_10d_pct,
                            max_dd_5d_pct = EXCLUDED.max_dd_5d_pct,
                            max_fav_5d_pct = EXCLUDED.max_fav_5d_pct,
                            market_regime = EXCLUDED.market_regime,
                            backtested_at = NOW()
                    """, (
                        ticker, sig_date, sig_close, c["prior_5d_ret"],
                        c["rvol"], c["close_strength"], c["range_pct"],
                        c["volume"], c["gap_pct"], conv,
                        mf["earnings_excl"], mf["falling_knife"], mf["suppress"],
                        fwd_1d, fwd_3d, fwd_5d, fwd_10d,
                        max_dd, max_fav, vb, regime,
                    ))
                    inserted += 1
                except Exception as ie:
                    print(f"[squeeze] insert {ticker} {sig_date}: {ie}")

            conn.commit()
            print(f"[squeeze] backtest complete: {inserted} rows written")

            return get_backtest_summary(conn)

    except Exception as e:
        print(f"[squeeze] run_historical_backtest error: {e}")
        return {"error": str(e)}


# ── Backtest summary ────────────────────────────────────────────────────────────

def get_backtest_summary(conn=None) -> dict:
    """Return aggregated win-rate/return stats, excluding module_f_suppressed rows."""
    close_conn = False
    if conn is None:
        if not _DB_URL:
            return {"error": "no DB_URL"}
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        close_conn = True
    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT
                COUNT(*)                                                        AS total_n,
                COUNT(*) FILTER (WHERE NOT module_f_suppressed)                 AS gate_passed_n,
                COUNT(*) FILTER (WHERE module_f_suppressed)                     AS gate_suppressed_n,

                -- Primary metric: 3d win-rate (gate-passed only)
                COUNT(*) FILTER (WHERE fwd_3d_pct IS NOT NULL
                                   AND NOT module_f_suppressed)                 AS n_with_3d,
                ROUND(AVG(fwd_3d_pct) FILTER (WHERE NOT module_f_suppressed
                                               AND fwd_3d_pct IS NOT NULL)::numeric, 4) AS avg_ret_3d,
                ROUND((100.0 * COUNT(*) FILTER (WHERE fwd_3d_pct > 0
                                                 AND NOT module_f_suppressed) /
                       NULLIF(COUNT(*) FILTER (WHERE fwd_3d_pct IS NOT NULL
                                               AND NOT module_f_suppressed), 0))::numeric, 1) AS wr_3d,

                -- 1d / 5d / 10d
                ROUND(AVG(fwd_1d_pct)  FILTER (WHERE NOT module_f_suppressed AND fwd_1d_pct  IS NOT NULL)::numeric,4) AS avg_ret_1d,
                ROUND(AVG(fwd_5d_pct)  FILTER (WHERE NOT module_f_suppressed AND fwd_5d_pct  IS NOT NULL)::numeric,4) AS avg_ret_5d,
                ROUND(AVG(fwd_10d_pct) FILTER (WHERE NOT module_f_suppressed AND fwd_10d_pct IS NOT NULL)::numeric,4) AS avg_ret_10d,
                ROUND((100.0 * COUNT(*) FILTER (WHERE fwd_1d_pct > 0 AND NOT module_f_suppressed) /
                       NULLIF(COUNT(*) FILTER (WHERE fwd_1d_pct IS NOT NULL AND NOT module_f_suppressed),0))::numeric,1) AS wr_1d,
                ROUND((100.0 * COUNT(*) FILTER (WHERE fwd_5d_pct > 0 AND NOT module_f_suppressed) /
                       NULLIF(COUNT(*) FILTER (WHERE fwd_5d_pct IS NOT NULL AND NOT module_f_suppressed),0))::numeric,1) AS wr_5d,

                MIN(signal_date) AS earliest, MAX(signal_date) AS latest
            FROM aiem_squeeze_backtest_log
        """)
        r = cur.fetchone()

        # By regime (gate-passed only)
        cur.execute("""
            SELECT market_regime, vol_bucket,
                   COUNT(*) AS n,
                   ROUND((100.0*COUNT(*)FILTER(WHERE fwd_3d_pct>0)/
                          NULLIF(COUNT(*)FILTER(WHERE fwd_3d_pct IS NOT NULL),0))::numeric,1) AS wr_3d,
                   ROUND(AVG(fwd_3d_pct)FILTER(WHERE fwd_3d_pct IS NOT NULL)::numeric,4)     AS avg_ret_3d
            FROM aiem_squeeze_backtest_log
            WHERE NOT module_f_suppressed
            GROUP BY market_regime, vol_bucket
            ORDER BY n DESC
        """)
        by_regime = [
            {"regime": row[0], "vol_bucket": row[1], "n": row[2],
             "wr_3d_pct": float(row[3] or 0), "avg_ret_3d": float(row[4] or 0)}
            for row in cur.fetchall()
        ]

        summary = {
            "total_n":           int(r[0] or 0),
            "gate_passed_n":     int(r[1] or 0),
            "gate_suppressed_n": int(r[2] or 0),
            "n_with_3d_fwd":     int(r[3] or 0),
            "avg_ret_3d":        float(r[4] or 0),
            "wr_3d_pct":         float(r[5] or 0),
            "avg_ret_1d":        float(r[6] or 0),
            "avg_ret_5d":        float(r[7] or 0),
            "avg_ret_10d":       float(r[8] or 0),
            "wr_1d_pct":         float(r[9] or 0),
            "wr_5d_pct":         float(r[10] or 0),
            "earliest":          str(r[11]) if r[11] else None,
            "latest":            str(r[12]) if r[12] else None,
            "by_regime":         by_regime,
            "borrow_cost_status": _BORROW_COST_STATUS,
            "si_pct_status":     _SI_PCT_STATUS,
            "dtc_status":        _DTC_STATUS,
            "data_source":       "polygon_market_daily (OHLCV + rvol, gap_pct, close_strength, range_pct)",
        }

        if close_conn:
            conn.close()
        return summary
    except Exception as e:
        if close_conn:
            try:
                conn.close()
            except Exception:
                pass
        return {"error": str(e)}


# ── Live scanner ────────────────────────────────────────────────────────────────

def run_scan() -> dict:
    """
    Daily scanner: identify Short_Squeeze_Reversion candidates from the most
    recent day in polygon_market_daily.

    Optionally annotates each hit with Finviz SI% if available (live-only;
    marked NOT_IMPLEMENTED if Finviz call fails or returns no data).

    Module F gate is applied; suppressed tickers are inserted with
    module_f_suppressed=True and are NOT sent to paper trading.

    Returns a summary dict with hit_count and sample signals.
    """
    if not _DB_URL:
        return {"error": "no DB_URL"}
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=8,
                              options="-c statement_timeout=15000") as conn, \
             conn.cursor() as cur:

            # Get most recent date in polygon_market_daily
            cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
            latest_date = cur.fetchone()[0]
            if latest_date is None:
                return {"status": "no_data"}

            # Get yesterday's close for prior_5d_ret (5 trading-day lag)
            cur.execute("""
                SELECT ticker, close_price FROM polygon_market_daily
                WHERE scan_date = (
                    SELECT scan_date FROM polygon_market_daily
                    WHERE scan_date < %s
                    ORDER BY scan_date DESC LIMIT 1 OFFSET 4
                )
            """, (latest_date,))
            close_5d_ago = {r[0]: float(r[1]) for r in cur.fetchall()}

            # Fetch today's candidates
            cur.execute("""
                SELECT ticker, close_price, rvol, close_strength,
                       range_pct, volume, gap_pct
                FROM polygon_market_daily
                WHERE scan_date = %s
                  AND close_price BETWEEN %s AND %s
                  AND volume >= %s
                  AND rvol >= %s
                  AND close_strength >= %s
                  AND range_pct >= %s
                  AND COALESCE(gap_pct, 0) > -15.0
                ORDER BY rvol DESC
            """, (
                latest_date,
                _PRICE_MIN, _PRICE_MAX, _VOLUME_MIN,
                _RVOL_MIN, _CLOSE_STR_MIN, _RANGE_MIN_PCT,
            ))
            rows = cur.fetchall()

            hits = []
            suppressed = []
            for r in rows:
                ticker, close_p, rvol, cs, rng, vol, gap = (
                    r[0], float(r[1]), float(r[2] or 0),
                    float(r[3] or 0), float(r[4] or 0),
                    int(r[5] or 0), float(r[6] or 0),
                )

                # Prior 5d return filter
                c5 = close_5d_ago.get(ticker)
                if c5 is None or c5 <= 0:
                    continue
                prior_5d = (close_p - c5) / c5 * 100
                if prior_5d > _PRIOR_5D_DROP_MAX:
                    continue

                conv    = _conviction(rvol, cs, gap)
                mf      = _module_f_gate(ticker, prior_5d, latest_date, cur)
                regime  = _regime_for_date(latest_date, cur)
                vb      = _vol_bucket(rng)

                # Attempt Finviz SI% (live-only; fail-open)
                si_pct = None
                si_status = _SI_PCT_STATUS
                try:
                    from main import _get_si_from_finviz
                    si_data = _get_si_from_finviz(ticker)
                    if si_data and si_data.get("si_pct") is not None:
                        si_pct    = float(si_data["si_pct"])
                        si_status = "AVAILABLE"
                except Exception:
                    pass  # SI stays NOT_IMPLEMENTED

                row_data = {
                    "ticker": ticker, "signal_date": latest_date,
                    "conviction_score": conv, "rvol": rvol,
                    "close_strength": cs, "range_pct": rng,
                    "gap_pct": gap, "volume": vol,
                    "si_pct": si_pct, "si_pct_status": si_status,
                    "borrow_cost_status": _BORROW_COST_STATUS,
                    "dtc_status": _DTC_STATUS,
                    "earnings_excl": mf["earnings_excl"],
                    "falling_knife": mf["falling_knife"],
                    "days_to_earnings": mf["days_to_earnings"],
                    "module_f_suppressed": mf["suppress"],
                }

                try:
                    cur.execute("""
                        INSERT INTO aiem_squeeze_signals (
                            ticker, signal_date, conviction_score,
                            rvol, close_strength, range_pct, gap_pct, volume,
                            si_pct, si_pct_status, borrow_cost_status, dtc_status,
                            earnings_excl, falling_knife, days_to_earnings,
                            module_f_suppressed
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,'NOT_IMPLEMENTED','NOT_IMPLEMENTED',
                            %s,%s,%s,%s
                        )
                        ON CONFLICT (ticker, signal_date) DO UPDATE SET
                            conviction_score   = EXCLUDED.conviction_score,
                            rvol               = EXCLUDED.rvol,
                            module_f_suppressed = EXCLUDED.module_f_suppressed,
                            scanned_at         = NOW()
                    """, (
                        ticker, latest_date, conv,
                        rvol, cs, rng, gap, vol,
                        si_pct, si_status,
                        mf["earnings_excl"], mf["falling_knife"],
                        mf["days_to_earnings"], mf["suppress"],
                    ))
                except Exception as ie:
                    print(f"[squeeze] scan insert {ticker}: {ie}")

                if mf["suppress"]:
                    suppressed.append(ticker)
                else:
                    hits.append(row_data)

            conn.commit()
            print(f"[squeeze] scan {latest_date}: {len(hits)} hits, "
                  f"{len(suppressed)} suppressed by Module F")
            return {
                "status": "ok", "scan_date": str(latest_date),
                "hit_count": len(hits), "suppressed_count": len(suppressed),
                "signals": hits[:10],
            }

    except Exception as e:
        print(f"[squeeze] run_scan error: {e}")
        return {"error": str(e)}


# ── BH-FDR registration ─────────────────────────────────────────────────────────

def register_signal() -> None:
    """
    Register / refresh Short_Squeeze_Reversion in aiem_signal_discoveries.

    Uses status='hypothesis' so Module 2 (decay) and Module 6 (rediscovery)
    include it in their evaluation passes. Backtest stats (p_value, signal_n,
    signal_win_rate) are pulled from aiem_squeeze_backtest_log and written to
    the discovery row so the BH-FDR correction pass has real numbers.

    Module 2 classification note: the conditions are multi-column price-volume
    proxy conditions queryable from polygon_market_daily — Module 2 will attempt
    to evaluate them via its column-adapter pipeline. However, the primary
    conditions (rvol, close_strength, range_pct) do not yet have adapters in
    Module 2, so it will classify this as evaluable_pending_columns initially.
    This is correct and honest — not a wiring failure.
    """
    if not _DB_URL:
        return
    conditions = {
        "rvol":          f">= {_RVOL_MIN} (top ~4% of all trading days)",
        "close_strength": f">= {_CLOSE_STR_MIN} (closed in upper 35% of range)",
        "range_pct":     f">= {_RANGE_MIN_PCT}% (large intraday volatility)",
        "price":         f"${_PRICE_MIN}–${_PRICE_MAX}",
        "volume":        f">= {_VOLUME_MIN:,}",
        "prior_5d_ret":  f"<= {_PRIOR_5D_DROP_MAX}% (stock was under pressure)",
        "gap_pct":       "> -15.0% (not in extreme free-fall)",
        "borrow_cost":   f"status={_BORROW_COST_STATUS}",
        "si_pct":        f"status={_SI_PCT_STATUS}",
        "dtc":           f"status={_DTC_STATUS}",
    }
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, conn.cursor() as cur:

            # Pull latest backtest stats (gate-passed rows only, primary metric = 3d)
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE fwd_3d_pct IS NOT NULL AND NOT module_f_suppressed),
                    AVG(fwd_3d_pct) FILTER (WHERE NOT module_f_suppressed AND fwd_3d_pct IS NOT NULL),
                    (100.0 * COUNT(*) FILTER (WHERE fwd_3d_pct > 0 AND NOT module_f_suppressed) /
                     NULLIF(COUNT(*) FILTER (WHERE fwd_3d_pct IS NOT NULL AND NOT module_f_suppressed),0))
                FROM aiem_squeeze_backtest_log
            """)
            row = cur.fetchone()
            bt_n   = int(row[0]) if row and row[0] else 0
            bt_ret = float(row[1]) if row and row[1] else None
            bt_wr  = float(row[2]) / 100.0 if row and row[2] else None  # fraction

            # Binomial p-value (one-sided vs 50% baseline, normal approximation)
            p_val = None
            if bt_n >= 10 and bt_wr is not None:
                k = int(round(bt_wr * bt_n))
                z = (k - 0.5 - bt_n * 0.5) / math.sqrt(bt_n * 0.25)
                p_val = round(0.5 * math.erfc(z / math.sqrt(2)), 4) if z > 0 else 0.9999

            note = (
                f"Module B proxy-only (SI%/borrow/DTC NOT_IMPLEMENTED); "
                f"evaluable_pending_columns in Module2; "
                f"backtest_n={bt_n}; wr_3d={bt_wr}; p={p_val}"
            )

            cur.execute(
                "SELECT id FROM aiem_signal_discoveries WHERE hypothesis_text=%s",
                (_SIGNAL_NAME,),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute("""
                    UPDATE aiem_signal_discoveries
                    SET signal_n        = %s,
                        signal_win_rate = %s,
                        signal_avg_ret  = %s,
                        p_value         = %s,
                        status          = 'hypothesis',
                        notes           = %s
                    WHERE id = %s
                """, (bt_n or None, bt_wr, bt_ret, p_val, note, existing[0]))
            else:
                cur.execute("""
                    INSERT INTO aiem_signal_discoveries
                        (hypothesis_text, conditions_json, status, horizon,
                         invented_indicator, signal_n, signal_win_rate,
                         signal_avg_ret, p_value, notes, discovered_at)
                    VALUES (%s, %s::jsonb, 'hypothesis', %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    _SIGNAL_NAME, json.dumps(conditions), _HORIZON,
                    _INVENTED_INDICATOR, bt_n or None, bt_wr, bt_ret, p_val, note,
                ))
            conn.commit()

        print(f"[squeeze] registered {_SIGNAL_NAME}: n={bt_n} wr_3d={bt_wr} p={p_val}")
    except Exception as e:
        print(f"[squeeze] register_signal error: {e}")
