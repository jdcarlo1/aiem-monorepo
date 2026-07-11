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

Short-interest data source (wired as of 2026-07-05)
----------------------------------------------------
Polygon /stocks/v1/short-interest endpoint.  FINRA bi-monthly settlement data.
Fields available:
  short_interest    — total shares sold short, not yet covered
  avg_daily_volume  — average daily trading volume at reporting date
  days_to_cover     — pre-computed ratio (short_interest / avg_daily_volume)
  settlement_date   — bi-monthly FINRA reporting date

Fields NOT available in this API:
  borrow_cost / utilization — CONFIRMED NOT_IMPLEMENTED after investigation;
    Polygon's SI endpoint does not include borrow rates. This field remains
    NOT_IMPLEMENTED and must not be implied as covered.

SI% derivation
--------------
  si_pct = short_interest / weighted_shares_outstanding × 100
  weighted_shares_outstanding sourced from Polygon /vX/reference/tickers/{ticker}.
  Computed at scan time for live signals; not stored in the backtest table
  (would require a separate per-ticker API call during historical replay).

Staleness policy (bi-monthly data gap)
---------------------------------------
  The module uses the most recent settlement_date record where
    settlement_date ≤ signal_date
  Maximum tolerated staleness: 45 calendar days (≈ 3 bi-monthly periods).
  If gap > 45 days:
    si_status = "TOO_STALE" (not AVAILABLE, not NOT_AVAILABLE)
    Row is excluded from the "with real SI" backtest cohort.
    Row remains in the full proxy-only backtest for comparison.

Module F gate (unchanged)
  1. Earnings exclusion: suppress if earnings within 5 calendar days.
  2. Falling-knife proxy: prior_5d_ret ≤ −20% = extreme real selling; suppress.

Backtest history
----------------
  Proxy-only (v1, 2026-07-05): n=138 gate-passed, WR_3d=40.6%, avg=-1.86%, p=0.9999
  Real SI (v2, this version):  see run_historical_backtest() output and
    aiem_signal_discoveries notes for the current numbers.

BH-FDR registration
  register_signal() writes status='hypothesis' to aiem_signal_discoveries.
  Status only moves to 'validated' if WR_3d > 50% with a defensible p < 0.05
  under BH-FDR correction.  Marginal improvement does not qualify.
"""

import bisect
import json
import math
import os
import time
import urllib.request
import datetime as dt
import psycopg2
from datetime import date, timedelta
from typing import Optional, Dict, List, Tuple, Any

_DB_URL  = os.environ.get("DATABASE_URL", "")
_POLY_KEY = os.environ.get("POLYGON_API_KEY", "")

# ── Signal identity ────────────────────────────────────────────────────────────
_SIGNAL_NAME        = "Short_Squeeze_Reversion"
_INVENTED_INDICATOR = "aiem_short_squeeze_v2"   # bumped: real SI data
_HORIZON            = "3d"

# ── Price/volume conditions (unchanged from v1) ───────────────────────────────
_RVOL_MIN            = 3.0
_CLOSE_STR_MIN       = 0.65
_RANGE_MIN_PCT       = 5.0
_PRICE_MIN           = 3.0
_PRICE_MAX           = 200.0
_VOLUME_MIN          = 300_000
_PRIOR_5D_DROP_MAX   = -3.0
_FALLING_KNIFE_FLOOR = -20.0

# ── Short-interest filters (new in v2) ────────────────────────────────────────
_DTC_MIN              = 3.0    # minimum days-to-cover for squeeze fuel
_SI_MAX_STALENESS_DAYS = 45   # most recent settlement must be ≤ 45 days old

# ── Status strings ─────────────────────────────────────────────────────────────
_BORROW_COST_STATUS   = "NOT_IMPLEMENTED"  # confirmed: not in Polygon SI API
_SI_NOT_AVAILABLE     = "NOT_AVAILABLE"    # Polygon has no record for this ticker
_SI_AVAILABLE         = "AVAILABLE"
_SI_TOO_STALE         = "TOO_STALE"       # record exists but > 45 days old

_FWD_HORIZONS = [1, 3, 5, 10]


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_schema() -> None:
    """Create/migrate all Module B tables."""
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, conn.cursor() as cur:
            # polygon_short_interest cache
            cur.execute("""
                CREATE TABLE IF NOT EXISTS polygon_short_interest (
                    ticker           TEXT NOT NULL,
                    settlement_date  DATE NOT NULL,
                    short_interest   BIGINT,
                    avg_daily_volume BIGINT,
                    days_to_cover    DOUBLE PRECISION,
                    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (ticker, settlement_date)
                )
            """)
            # backtest log — add SI columns if they don't exist
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_squeeze_backtest_log (
                    id                   BIGSERIAL PRIMARY KEY,
                    ticker               TEXT NOT NULL,
                    signal_date          DATE NOT NULL,
                    signal_close         DOUBLE PRECISION,
                    prior_5d_ret         DOUBLE PRECISION,
                    rvol                 DOUBLE PRECISION,
                    close_strength       DOUBLE PRECISION,
                    range_pct            DOUBLE PRECISION,
                    volume               BIGINT,
                    gap_pct              DOUBLE PRECISION,
                    conviction_score     DOUBLE PRECISION,
                    -- SI columns (v2)
                    si_settlement_date   DATE,
                    si_staleness_days    INT,
                    si_status            TEXT NOT NULL DEFAULT 'NOT_AVAILABLE',
                    short_interest       BIGINT,
                    avg_daily_volume_si  BIGINT,
                    days_to_cover        DOUBLE PRECISION,
                    si_pct               DOUBLE PRECISION,
                    -- always NOT_IMPLEMENTED (confirmed: not in Polygon SI API)
                    borrow_cost_status   TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    -- Module F
                    earnings_excl        BOOLEAN NOT NULL DEFAULT FALSE,
                    falling_knife        BOOLEAN NOT NULL DEFAULT FALSE,
                    module_f_suppressed  BOOLEAN NOT NULL DEFAULT FALSE,
                    -- forward returns
                    fwd_1d_pct           DOUBLE PRECISION,
                    fwd_3d_pct           DOUBLE PRECISION,
                    fwd_5d_pct           DOUBLE PRECISION,
                    fwd_10d_pct          DOUBLE PRECISION,
                    max_dd_5d_pct        DOUBLE PRECISION,
                    max_fav_5d_pct       DOUBLE PRECISION,
                    vol_bucket           TEXT,
                    market_regime        TEXT,
                    backtested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (ticker, signal_date)
                )
            """)
            # Add SI columns to existing rows (idempotent)
            for col, defn in [
                ("si_settlement_date",  "DATE"),
                ("si_staleness_days",   "INT"),
                ("si_status",           "TEXT NOT NULL DEFAULT 'NOT_AVAILABLE'"),
                ("short_interest",      "BIGINT"),
                ("avg_daily_volume_si", "BIGINT"),
                ("days_to_cover",       "DOUBLE PRECISION"),
                ("si_pct",              "DOUBLE PRECISION"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE aiem_squeeze_backtest_log ADD COLUMN IF NOT EXISTS {col} {defn}")
                except Exception:
                    pass

            # live signal table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_squeeze_signals (
                    id                   BIGSERIAL PRIMARY KEY,
                    ticker               TEXT NOT NULL,
                    signal_date          DATE NOT NULL,
                    conviction_score     DOUBLE PRECISION,
                    rvol                 DOUBLE PRECISION,
                    close_strength       DOUBLE PRECISION,
                    range_pct            DOUBLE PRECISION,
                    gap_pct              DOUBLE PRECISION,
                    volume               BIGINT,
                    -- SI (v2)
                    si_settlement_date   DATE,
                    si_staleness_days    INT,
                    si_status            TEXT NOT NULL DEFAULT 'NOT_AVAILABLE',
                    short_interest       BIGINT,
                    avg_daily_volume_si  BIGINT,
                    days_to_cover        DOUBLE PRECISION,
                    si_pct               DOUBLE PRECISION,
                    -- borrow cost: confirmed not in Polygon SI API
                    borrow_cost_status   TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED',
                    earnings_excl        BOOLEAN NOT NULL DEFAULT FALSE,
                    falling_knife        BOOLEAN NOT NULL DEFAULT FALSE,
                    days_to_earnings     INT,
                    module_f_suppressed  BOOLEAN NOT NULL DEFAULT FALSE,
                    scanned_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (ticker, signal_date)
                )
            """)
            conn.commit()
        print("[squeeze] schema OK")
    except Exception as e:
        print(f"[squeeze] init_schema error: {e}")


# ── Polygon SI lookup ───────────────────────────────────────────────────────────

def _fetch_si_for_ticker(ticker: str, from_date: str = "2024-01-01") -> list:
    """
    Fetch short-interest records from Polygon /stocks/v1/short-interest.
    Returns list of dicts: {settlement_date, short_interest, avg_daily_volume, days_to_cover}.
    Returns [] if ticker has no coverage or API is unavailable.
    """
    if not _POLY_KEY:
        return []
    url = (f"https://api.polygon.io/stocks/v1/short-interest"
           f"?ticker={ticker}&settlement_date.gte={from_date}&limit=50&apiKey={_POLY_KEY}")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("results", [])
    except Exception:
        return []


def _get_si_for_signal(ticker: str, sig_date: date, cur) -> dict:
    """
    Look up the most recent SI record in polygon_short_interest where
    settlement_date ≤ sig_date.

    Returns dict with:
      si_status           — AVAILABLE | TOO_STALE | NOT_AVAILABLE
      si_settlement_date  — date of the record used
      si_staleness_days   — sig_date - settlement_date
      short_interest      — shares sold short
      avg_daily_volume_si — avg daily vol at reporting date
      days_to_cover       — pre-computed DTC
      si_pct              — None (not derivable without shares_outstanding in DB)
    """
    out = {
        "si_status": _SI_NOT_AVAILABLE,
        "si_settlement_date": None,
        "si_staleness_days": None,
        "short_interest": None,
        "avg_daily_volume_si": None,
        "days_to_cover": None,
        "si_pct": None,
    }
    try:
        cur.execute("""
            SELECT settlement_date, short_interest, avg_daily_volume, days_to_cover
            FROM polygon_short_interest
            WHERE ticker = %s AND settlement_date <= %s
            ORDER BY settlement_date DESC LIMIT 1
        """, (ticker, sig_date))
        row = cur.fetchone()
        if row:
            staleness = (sig_date - row[0]).days
            out["si_settlement_date"] = row[0]
            out["si_staleness_days"] = staleness
            out["short_interest"] = row[1]
            out["avg_daily_volume_si"] = row[2]
            out["days_to_cover"] = float(row[3]) if row[3] is not None else None
            if staleness <= _SI_MAX_STALENESS_DAYS:
                out["si_status"] = _SI_AVAILABLE
            else:
                out["si_status"] = _SI_TOO_STALE
    except Exception:
        pass
    return out


def _get_shares_outstanding(ticker: str) -> Optional[float]:
    """
    Fetch weighted_shares_outstanding from Polygon /vX/reference/tickers/{ticker}.
    Used to compute si_pct = short_interest / shares_outstanding × 100.
    Returns None if unavailable.
    """
    if not _POLY_KEY:
        return None
    url = f"https://api.polygon.io/vX/reference/tickers/{ticker}?apiKey={_POLY_KEY}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            r = json.loads(resp.read()).get("results", {})
        shares = r.get("weighted_shares_outstanding") or r.get("share_class_shares_outstanding")
        return float(shares) if shares else None
    except Exception:
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _vol_bucket(range_pct: float) -> str:
    if range_pct >= 10.0:
        return "HIGH_VOL"
    if range_pct >= 5.0:
        return "MEDIUM_VOL"
    return "LOW_VOL"


def _regime_for_date(sig_date: date, cur) -> str:
    try:
        cur.execute("""
            SELECT vix_close FROM vix_daily
            WHERE scan_date <= %s ORDER BY scan_date DESC LIMIT 1
        """, (sig_date,))
        row = cur.fetchone()
        if row and row[0]:
            v = float(row[0])
            return "TREND_DOWN" if v >= 25 else ("CHOPPY" if v >= 18 else "TREND_UP")
    except Exception:
        pass
    return "NO_VIX_DATA"


def _conviction(rvol: float, close_strength: float, gap_pct: float,
                dtc: Optional[float] = None) -> float:
    """
    Score 0–10. v2 adds +1 for high DTC (squeeze fuel confirmed).
    """
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
    # DTC bonus: real squeeze fuel confirmed
    if dtc is not None:
        if dtc >= 10.0:
            score += 1.5
        elif dtc >= 5.0:
            score += 1.0
        elif dtc >= _DTC_MIN:
            score += 0.5
    return round(max(0.0, min(10.0, score)), 2)


def _module_f_gate(ticker: str, prior_5d_ret: float, sig_date: date, cur) -> dict:
    out = {
        "suppress": False,
        "earnings_excl": False,
        "falling_knife": False,
        "days_to_earnings": None,
    }
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
        pass
    if prior_5d_ret is not None and prior_5d_ret <= _FALLING_KNIFE_FLOOR:
        out["falling_knife"] = True
        out["suppress"] = True
    return out


# ── SI cache backfill ──────────────────────────────────────────────────────────

def backfill_si_for_backtest(max_tickers: int = 232) -> dict:
    """
    For each ticker in aiem_squeeze_backtest_log that has no entry in
    polygon_short_interest, fetch from Polygon and cache.
    Rate-limited to 1 req / 8s.  Safe to call repeatedly.
    """
    if not _DB_URL or not _POLY_KEY:
        return {"error": "missing DB_URL or POLYGON_API_KEY"}
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM polygon_short_interest")
            have = {r[0] for r in cur.fetchall()}
            cur.execute("SELECT DISTINCT ticker FROM aiem_squeeze_backtest_log ORDER BY ticker")
            todo = [r[0] for r in cur.fetchall() if r[0] not in have][:max_tickers]

            ok = 0; empty = 0; inserted = 0
            for i, ticker in enumerate(todo):
                time.sleep(8)
                results = _fetch_si_for_ticker(ticker)
                if results:
                    ok += 1
                    for r in results:
                        try:
                            cur.execute("""
                                INSERT INTO polygon_short_interest
                                    (ticker, settlement_date, short_interest, avg_daily_volume, days_to_cover)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (ticker, settlement_date) DO NOTHING
                            """, (ticker, r["settlement_date"], r.get("short_interest"),
                                  r.get("avg_daily_volume"), r.get("days_to_cover")))
                            inserted += 1
                        except Exception:
                            pass
                    conn.commit()
                else:
                    empty += 1

            return {"ok": ok, "empty": empty, "inserted": inserted, "remaining": len(todo) - ok - empty}
    except Exception as e:
        return {"error": str(e)}


# ── Historical backtest ─────────────────────────────────────────────────────────

def run_historical_backtest(force: bool = False) -> dict:
    """
    Backtest Short_Squeeze_Reversion on polygon_market_daily.

    v2 changes vs v1:
    - Joins polygon_short_interest for real DTC data per signal row
    - Adds si_status (AVAILABLE | TOO_STALE | NOT_AVAILABLE) per row
    - Reports three cohorts:
        A. All gate-passed (proxy-only, same as v1 for comparison)
        B. Gate-passed + si_status=AVAILABLE (real SI data present)
        C. Gate-passed + si_status=AVAILABLE + days_to_cover >= DTC_MIN (full filter)
    - borrow_cost_status = NOT_IMPLEMENTED on every row (confirmed: not in Polygon API)
    """
    if not _DB_URL:
        return {"error": "no DB_URL"}

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=10,
                              options="-c statement_timeout=120000") as conn, \
             conn.cursor() as cur:

            if not force:
                cur.execute("SELECT COUNT(*) FROM aiem_squeeze_backtest_log")
                if cur.fetchone()[0] > 0:
                    # Already populated — just refresh SI columns
                    _refresh_si_columns(conn, cur)
                    conn.commit()
                    return get_backtest_summary(conn)

            print("[squeeze] fetching candidates from polygon_market_daily…")
            cur.execute("""
                WITH prior_prices AS (
                    SELECT
                        p.ticker, p.scan_date, p.close_price, p.open_price,
                        p.rvol, p.close_strength, p.range_pct, p.volume, p.gap_pct,
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
                {"ticker": r[0], "signal_date": r[1], "signal_close": float(r[2]),
                 "rvol": float(r[3]), "close_strength": float(r[4]),
                 "range_pct": float(r[5]), "volume": int(r[6]),
                 "gap_pct": float(r[7] or 0), "prior_5d_ret": float(r[8] or 0)}
                for r in cur.fetchall()
            ]
            print(f"[squeeze] {len(candidates)} candidates")

            if not candidates:
                return {"status": "no_candidates"}

            # Batch-fetch forward prices
            tickers = list({c["ticker"] for c in candidates})
            min_date = min(c["signal_date"] for c in candidates)
            max_date = max(c["signal_date"] for c in candidates)

            cur.execute("""
                SELECT ticker, scan_date, close_price, high_price, low_price
                FROM polygon_market_daily
                WHERE ticker = ANY(%s)
                  AND scan_date > %s AND scan_date <= %s
                ORDER BY ticker, scan_date
            """, (tickers, min_date, max_date + timedelta(days=22)))

            price_map: Dict[str, List] = {}
            for r in cur.fetchall():
                price_map.setdefault(r[0], []).append(
                    (r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0)))

            inserted = 0
            for c in candidates:
                ticker    = c["ticker"]
                sig_date  = c["signal_date"]
                sig_close = c["signal_close"]

                mf     = _module_f_gate(ticker, c["prior_5d_ret"], sig_date, cur)
                si_d   = _get_si_for_signal(ticker, sig_date, cur)
                dtc    = si_d["days_to_cover"]
                conv   = _conviction(c["rvol"], c["close_strength"], c["gap_pct"], dtc)
                regime = _regime_for_date(sig_date, cur)
                vb     = _vol_bucket(c["range_pct"])

                futures    = price_map.get(ticker, [])
                dates_list = [f[0] for f in futures]
                idx        = bisect.bisect_right(dates_list, sig_date)

                def _fwd(offset):
                    i = idx + offset - 1
                    if i < len(futures) and sig_close > 0:
                        return round((futures[i][1] - sig_close) / sig_close * 100, 4)
                    return None

                fwd_1d, fwd_3d, fwd_5d, fwd_10d = _fwd(1), _fwd(3), _fwd(5), _fwd(10)
                max_dd = max_fav = None
                if idx < len(futures) and sig_close > 0:
                    window = futures[idx: idx + 5]
                    if window:
                        max_dd  = round((min(f[3] for f in window) - sig_close) / sig_close * 100, 4)
                        max_fav = round((max(f[2] for f in window) - sig_close) / sig_close * 100, 4)

                try:
                    cur.execute("""
                        INSERT INTO aiem_squeeze_backtest_log (
                            ticker, signal_date, signal_close, prior_5d_ret,
                            rvol, close_strength, range_pct, volume, gap_pct,
                            conviction_score,
                            si_settlement_date, si_staleness_days, si_status,
                            short_interest, avg_daily_volume_si, days_to_cover, si_pct,
                            borrow_cost_status,
                            earnings_excl, falling_knife, module_f_suppressed,
                            fwd_1d_pct, fwd_3d_pct, fwd_5d_pct, fwd_10d_pct,
                            max_dd_5d_pct, max_fav_5d_pct, vol_bucket, market_regime
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s,
                            'NOT_IMPLEMENTED',
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        ON CONFLICT (ticker, signal_date) DO UPDATE SET
                            si_settlement_date  = EXCLUDED.si_settlement_date,
                            si_staleness_days   = EXCLUDED.si_staleness_days,
                            si_status           = EXCLUDED.si_status,
                            short_interest      = EXCLUDED.short_interest,
                            avg_daily_volume_si = EXCLUDED.avg_daily_volume_si,
                            days_to_cover       = EXCLUDED.days_to_cover,
                            conviction_score    = EXCLUDED.conviction_score,
                            backtested_at       = NOW()
                    """, (
                        ticker, sig_date, sig_close, c["prior_5d_ret"],
                        c["rvol"], c["close_strength"], c["range_pct"],
                        c["volume"], c["gap_pct"], conv,
                        si_d["si_settlement_date"], si_d["si_staleness_days"],
                        si_d["si_status"], si_d["short_interest"],
                        si_d["avg_daily_volume_si"], dtc, si_d["si_pct"],
                        mf["earnings_excl"], mf["falling_knife"], mf["suppress"],
                        fwd_1d, fwd_3d, fwd_5d, fwd_10d, max_dd, max_fav, vb, regime,
                    ))
                    inserted += 1
                except Exception as ie:
                    print(f"[squeeze] insert {ticker} {sig_date}: {ie}")

            conn.commit()
            print(f"[squeeze] backtest: {inserted} rows written/updated")
            return get_backtest_summary(conn)

    except Exception as e:
        print(f"[squeeze] run_historical_backtest error: {e}")
        return {"error": str(e)}


def _refresh_si_columns(conn, cur) -> None:
    """Update SI columns on existing backtest rows using cached polygon_short_interest."""
    cur.execute("SELECT DISTINCT ticker, signal_date FROM aiem_squeeze_backtest_log")
    rows = cur.fetchall()
    updated = 0
    for ticker, sig_date in rows:
        si_d = _get_si_for_signal(ticker, sig_date, cur)
        cur.execute("""
            UPDATE aiem_squeeze_backtest_log
            SET si_settlement_date = %s,
                si_staleness_days  = %s,
                si_status          = %s,
                short_interest     = %s,
                avg_daily_volume_si = %s,
                days_to_cover      = %s
            WHERE ticker = %s AND signal_date = %s
        """, (
            si_d["si_settlement_date"], si_d["si_staleness_days"], si_d["si_status"],
            si_d["short_interest"], si_d["avg_daily_volume_si"], si_d["days_to_cover"],
            ticker, sig_date,
        ))
        updated += 1
    print(f"[squeeze] refreshed SI columns on {updated} rows")


# ── Backtest summary ────────────────────────────────────────────────────────────

def get_backtest_summary(conn=None) -> dict:
    """
    Three-cohort summary:
      A = all gate-passed (proxy-only, v1 baseline)
      B = gate-passed + si_status=AVAILABLE
      C = gate-passed + si_status=AVAILABLE + days_to_cover >= DTC_MIN
    """
    close_conn = False
    if conn is None:
        if not _DB_URL:
            return {"error": "no DB_URL"}
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        close_conn = True
    try:
        cur = conn.cursor()

        def _stats(where_extra: str = "") -> dict:
            cur.execute(f"""
                SELECT
                    COUNT(*) FILTER (WHERE fwd_3d_pct IS NOT NULL)      AS n,
                    ROUND((100.0 * COUNT(*) FILTER (WHERE fwd_3d_pct > 0) /
                           NULLIF(COUNT(*) FILTER (WHERE fwd_3d_pct IS NOT NULL), 0))::numeric, 2) AS wr_3d,
                    ROUND(AVG(fwd_3d_pct) FILTER (WHERE fwd_3d_pct IS NOT NULL)::numeric, 4)       AS avg_3d,
                    ROUND((100.0 * COUNT(*) FILTER (WHERE fwd_1d_pct > 0) /
                           NULLIF(COUNT(*) FILTER (WHERE fwd_1d_pct IS NOT NULL), 0))::numeric, 2) AS wr_1d,
                    ROUND(AVG(fwd_1d_pct) FILTER (WHERE fwd_1d_pct IS NOT NULL)::numeric, 4)       AS avg_1d,
                    ROUND((100.0 * COUNT(*) FILTER (WHERE fwd_5d_pct > 0) /
                           NULLIF(COUNT(*) FILTER (WHERE fwd_5d_pct IS NOT NULL), 0))::numeric, 2) AS wr_5d,
                    ROUND(AVG(fwd_5d_pct) FILTER (WHERE fwd_5d_pct IS NOT NULL)::numeric, 4)       AS avg_5d
                FROM aiem_squeeze_backtest_log
                WHERE NOT module_f_suppressed {where_extra}
            """)
            r = cur.fetchone()
            return {
                "n": int(r[0] or 0), "wr_3d_pct": float(r[1] or 0),
                "avg_ret_3d": float(r[2] or 0),
                "wr_1d_pct": float(r[3] or 0), "avg_ret_1d": float(r[4] or 0),
                "wr_5d_pct": float(r[5] or 0), "avg_ret_5d": float(r[6] or 0),
            }

        cohort_a = _stats()                                                        # proxy-only
        cohort_b = _stats("AND si_status = 'AVAILABLE'")                          # real SI
        cohort_c = _stats(f"AND si_status = 'AVAILABLE' AND days_to_cover >= {_DTC_MIN}")  # full filter

        # SI coverage summary
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE si_status = 'AVAILABLE')     AS si_available,
                COUNT(*) FILTER (WHERE si_status = 'TOO_STALE')     AS si_stale,
                COUNT(*) FILTER (WHERE si_status = 'NOT_AVAILABLE') AS si_missing,
                MAX(si_staleness_days) FILTER (WHERE si_status='AVAILABLE') AS max_staleness,
                ROUND(AVG(days_to_cover) FILTER (WHERE si_status='AVAILABLE')::numeric, 2) AS avg_dtc
            FROM aiem_squeeze_backtest_log
            WHERE NOT module_f_suppressed
        """)
        si_cov = cur.fetchone()

        # p-value helper (one-sided binomial normal approx vs 50%)
        def _pval(n, wr_pct):
            if n < 10 or wr_pct is None:
                return None
            k = round(wr_pct / 100 * n)
            z = (k - 0.5 - n * 0.5) / math.sqrt(n * 0.25)
            if z <= 0:
                return 0.9999
            return round(0.5 * math.erfc(z / math.sqrt(2)), 4)

        summary = {
            "cohort_A_proxy_only": {**cohort_a,
                "p_value": _pval(cohort_a["n"], cohort_a["wr_3d_pct"]),
                "description": "All gate-passed rows (v1 baseline, no SI filter)"},
            "cohort_B_real_SI": {**cohort_b,
                "p_value": _pval(cohort_b["n"], cohort_b["wr_3d_pct"]),
                "description": f"Gate-passed + si_status=AVAILABLE (staleness ≤ {_SI_MAX_STALENESS_DAYS}d)"},
            "cohort_C_high_DTC": {**cohort_c,
                "p_value": _pval(cohort_c["n"], cohort_c["wr_3d_pct"]),
                "description": f"Gate-passed + si_status=AVAILABLE + DTC ≥ {_DTC_MIN}"},
            "si_coverage": {
                "available":    int(si_cov[0] or 0),
                "too_stale":    int(si_cov[1] or 0),
                "not_available": int(si_cov[2] or 0),
                "max_staleness_days": int(si_cov[3] or 0) if si_cov[3] else None,
                "avg_dtc_where_available": float(si_cov[4] or 0) if si_cov[4] else None,
            },
            "borrow_cost_status": _BORROW_COST_STATUS,
            "si_pct_note": (
                "si_pct = short_interest / weighted_shares_outstanding × 100. "
                "weighted_shares_outstanding from Polygon /vX/reference/tickers/{ticker}. "
                "Not stored in backtest table (would require per-ticker API call during "
                "historical replay). Available in live scan output."
            ),
            "staleness_policy": (
                f"Most recent settlement_date ≤ signal_date used. "
                f"Max tolerated gap: {_SI_MAX_STALENESS_DAYS} days. "
                f"Gap > {_SI_MAX_STALENESS_DAYS}d → si_status='TOO_STALE', excluded from cohorts B/C."
            ),
            "data_source": "Polygon /stocks/v1/short-interest (FINRA bi-monthly)",
        }
        if close_conn:
            conn.close()
        return summary
    except Exception as e:
        if close_conn:
            try: conn.close()
            except: pass
        return {"error": str(e)}


# ── Live scanner ────────────────────────────────────────────────────────────────

def run_scan() -> dict:
    """
    Daily scanner with real Polygon SI data.
    For each candidate:
      1. Checks Module F gate.
      2. Looks up most recent SI from polygon_short_interest (or fetches live if missing).
      3. Computes si_pct via shares_outstanding lookup.
      4. si_status = AVAILABLE | TOO_STALE | NOT_AVAILABLE.
      5. borrow_cost_status = NOT_IMPLEMENTED (confirmed: not in API).
      6. Inserts into aiem_squeeze_signals.
    """
    if not _DB_URL:
        return {"error": "no DB_URL"}
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=8,
                              options="-c statement_timeout=15000") as conn, \
             conn.cursor() as cur:

            cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
            latest_date = cur.fetchone()[0]
            if latest_date is None:
                return {"status": "no_data"}

            cur.execute("""
                SELECT ticker, close_price FROM polygon_market_daily
                WHERE scan_date = (
                    SELECT scan_date FROM polygon_market_daily
                    WHERE scan_date < %s ORDER BY scan_date DESC LIMIT 1 OFFSET 4
                )
            """, (latest_date,))
            close_5d_ago = {r[0]: float(r[1]) for r in cur.fetchall()}

            cur.execute("""
                SELECT ticker, close_price, rvol, close_strength, range_pct, volume, gap_pct
                FROM polygon_market_daily
                WHERE scan_date = %s
                  AND close_price BETWEEN %s AND %s
                  AND volume >= %s
                  AND rvol >= %s
                  AND close_strength >= %s
                  AND range_pct >= %s
                  AND COALESCE(gap_pct, 0) > -15.0
                ORDER BY rvol DESC
            """, (latest_date, _PRICE_MIN, _PRICE_MAX, _VOLUME_MIN,
                  _RVOL_MIN, _CLOSE_STR_MIN, _RANGE_MIN_PCT))
            rows = cur.fetchall()

            hits = []; suppressed = []
            for r in rows:
                ticker = r[0]
                close_p, rvol, cs, rng, vol, gap = (
                    float(r[1]), float(r[2] or 0), float(r[3] or 0),
                    float(r[4] or 0), int(r[5] or 0), float(r[6] or 0))

                c5 = close_5d_ago.get(ticker)
                if c5 is None or c5 <= 0:
                    continue
                prior_5d = (close_p - c5) / c5 * 100
                if prior_5d > _PRIOR_5D_DROP_MAX:
                    continue

                mf   = _module_f_gate(ticker, prior_5d, latest_date, cur)
                si_d = _get_si_for_signal(ticker, latest_date, cur)

                # If not in cache, attempt a live fetch (rate-limit aware: max 1 per ticker)
                if si_d["si_status"] == _SI_NOT_AVAILABLE and _POLY_KEY:
                    live = _fetch_si_for_ticker(ticker)
                    if live:
                        for lr in live:
                            try:
                                cur.execute("""
                                    INSERT INTO polygon_short_interest
                                        (ticker, settlement_date, short_interest, avg_daily_volume, days_to_cover)
                                    VALUES (%s,%s,%s,%s,%s)
                                    ON CONFLICT DO NOTHING
                                """, (ticker, lr["settlement_date"], lr.get("short_interest"),
                                      lr.get("avg_daily_volume"), lr.get("days_to_cover")))
                            except Exception:
                                pass
                        conn.commit()
                        si_d = _get_si_for_signal(ticker, latest_date, cur)

                # Compute si_pct if data available
                si_pct = None
                if si_d["si_status"] == _SI_AVAILABLE and si_d["short_interest"]:
                    shares = _get_shares_outstanding(ticker)
                    if shares and shares > 0:
                        si_pct = round(si_d["short_interest"] / shares * 100, 2)

                dtc  = si_d["days_to_cover"]
                conv = _conviction(rvol, cs, gap, dtc)

                try:
                    cur.execute("""
                        INSERT INTO aiem_squeeze_signals (
                            ticker, signal_date, conviction_score,
                            rvol, close_strength, range_pct, gap_pct, volume,
                            si_settlement_date, si_staleness_days, si_status,
                            short_interest, avg_daily_volume_si, days_to_cover, si_pct,
                            borrow_cost_status,
                            earnings_excl, falling_knife, days_to_earnings, module_f_suppressed
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s,
                            'NOT_IMPLEMENTED',
                            %s,%s,%s,%s
                        )
                        ON CONFLICT (ticker, signal_date) DO UPDATE SET
                            si_status        = EXCLUDED.si_status,
                            days_to_cover    = EXCLUDED.days_to_cover,
                            si_pct           = EXCLUDED.si_pct,
                            conviction_score = EXCLUDED.conviction_score,
                            scanned_at       = NOW()
                    """, (
                        ticker, latest_date, conv, rvol, cs, rng, gap, vol,
                        si_d["si_settlement_date"], si_d["si_staleness_days"],
                        si_d["si_status"], si_d["short_interest"],
                        si_d["avg_daily_volume_si"], dtc, si_pct,
                        mf["earnings_excl"], mf["falling_knife"],
                        mf["days_to_earnings"], mf["suppress"],
                    ))
                except Exception as ie:
                    print(f"[squeeze] scan insert {ticker}: {ie}")

                if mf["suppress"]:
                    suppressed.append(ticker)
                else:
                    hits.append({
                        "ticker": ticker, "signal_date": str(latest_date),
                        "conviction_score": conv, "rvol": rvol,
                        "close_strength": cs, "range_pct": rng,
                        "si_status": si_d["si_status"],
                        "days_to_cover": dtc, "si_pct": si_pct,
                        "borrow_cost_status": _BORROW_COST_STATUS,
                    })

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
    Register/refresh Short_Squeeze_Reversion in aiem_signal_discoveries.
    Uses the three-cohort summary. Status moves to 'validated' ONLY if
    WR_3d > 50% with p < 0.05 in cohort C (the highest-integrity cohort).
    Marginal improvement does not qualify.
    """
    if not _DB_URL:
        return
    conditions = {
        "rvol":              f">= {_RVOL_MIN}",
        "close_strength":    f">= {_CLOSE_STR_MIN}",
        "range_pct":         f">= {_RANGE_MIN_PCT}%",
        "price":             f"${_PRICE_MIN}–${_PRICE_MAX}",
        "volume":            f">= {_VOLUME_MIN:,}",
        "prior_5d_ret":      f"<= {_PRIOR_5D_DROP_MAX}%",
        "days_to_cover":     f">= {_DTC_MIN} (Polygon FINRA bi-monthly)",
        "si_data_source":    "Polygon /stocks/v1/short-interest",
        "borrow_cost":       f"status={_BORROW_COST_STATUS} (not in Polygon SI API)",
        "staleness_policy":  f"most recent settlement ≤ signal_date, max {_SI_MAX_STALENESS_DAYS}d",
    }
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, conn.cursor() as cur:
            summary = get_backtest_summary(conn)

            # Primary metric from cohort C (real SI + DTC filter)
            c = summary.get("cohort_C_high_DTC", {})
            bt_n   = c.get("n", 0)
            bt_wr  = c.get("wr_3d_pct")
            bt_ret = c.get("avg_ret_3d")
            p_val  = c.get("p_value")

            # Also pull cohort A for comparison note
            ca = summary.get("cohort_A_proxy_only", {})
            cb = summary.get("cohort_B_real_SI", {})
            si_cov = summary.get("si_coverage", {})

            new_status = "hypothesis"  # never auto-promote; requires human review
            note = (
                f"v2 (real Polygon SI data, 2026-07-05). "
                f"CohortA(proxy-only): n={ca.get('n')} WR={ca.get('wr_3d_pct')}% p={ca.get('p_value')}. "
                f"CohortB(SI_avail): n={cb.get('n')} WR={cb.get('wr_3d_pct')}% p={cb.get('p_value')}. "
                f"CohortC(SI+DTC>={_DTC_MIN}): n={bt_n} WR={bt_wr}% p={p_val}. "
                f"SI_coverage: avail={si_cov.get('available')} stale={si_cov.get('too_stale')} "
                f"missing={si_cov.get('not_available')}. "
                f"borrow_cost=NOT_IMPLEMENTED (confirmed: not in Polygon SI API). "
                f"si_pct_formula: short_interest/weighted_shares_outstanding*100."
            )

            cur.execute(
                "SELECT id FROM aiem_signal_discoveries WHERE hypothesis_text=%s",
                (_SIGNAL_NAME,))
            existing = cur.fetchone()
            if existing:
                cur.execute("""
                    UPDATE aiem_signal_discoveries
                    SET signal_n        = %s,
                        signal_win_rate = %s,
                        signal_avg_ret  = %s,
                        p_value         = %s,
                        status          = %s,
                        notes           = %s,
                        invented_indicator = %s
                    WHERE id = %s
                """, (bt_n or None, (bt_wr / 100) if bt_wr else None,
                      bt_ret, p_val, new_status, note, _INVENTED_INDICATOR, existing[0]))
            else:
                cur.execute("""
                    INSERT INTO aiem_signal_discoveries
                        (hypothesis_text, conditions_json, status, horizon,
                         invented_indicator, signal_n, signal_win_rate,
                         signal_avg_ret, p_value, notes, signal_name, discovered_at)
                    VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (_SIGNAL_NAME, json.dumps(conditions), new_status, _HORIZON,
                      _INVENTED_INDICATOR, bt_n or None,
                      (bt_wr / 100) if bt_wr else None,
                      bt_ret, p_val, note, _SIGNAL_NAME))
            conn.commit()

        print(f"[squeeze] registered {_SIGNAL_NAME}: "
              f"cohortC n={bt_n} wr={bt_wr}% p={p_val} status={new_status}")
    except Exception as e:
        print(f"[squeeze] register_signal error: {e}")
