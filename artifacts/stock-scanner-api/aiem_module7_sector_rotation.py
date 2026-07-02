"""
aiem_module7_sector_rotation.py — Module 7: Sector Rotation Detector

Daily scan of sector ETF data from polygon_market_daily to detect which sectors
are heating up or cooling off. Surfaces rotation as tiered-confidence signals:
  Tier 1 — Early move     (logged only, no alert)
  Tier 2 — Developing     (Telegram alert, watch)
  Tier 3 — Confirmed      (Telegram alert, feeds conviction weighting)

Data source: polygon_market_daily (all 13 tickers present with 498 days of history).
No external API calls required.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("module7_sector_rotation")

# ---------------------------------------------------------------------------
# Universe

_SECTORS = {
    "XLK":  "Technology",
    "SMH":  "Semiconductors",
    "XLF":  "Financials",
    "XLRE": "Real Estate",
    "XLE":  "Energy",
    "XLV":  "Healthcare",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLU":  "Utilities",
    "XLB":  "Materials",
    "XLC":  "Communication Services",
}
_SECTOR_TICKERS = list(_SECTORS.keys())
_ALL_TICKERS    = ["SPY"] + _SECTOR_TICKERS

# Tier thresholds
_T1_SD_MULTIPLIER    = 1.5   # Tier 1: 1d RS > 1.5 SD from own 60-day RS distribution
_T1_RANK_JUMP        = 4     # Tier 1: rank jump >= 4 positions in 1 day
_T2_VOL_RATIO_MIN    = 1.2   # Tier 2: volume ratio > 1.2x on ≥2 of last 3 days
_T2_VOL_MIN_DAYS     = 2     # Tier 2: min days with vol > _T2_VOL_RATIO_MIN
_T3_CONSEC_RANK_DAYS = 4     # Tier 3: consecutive days in top/bottom 3
_T3_TOP_N            = 3     # Tier 3: top/bottom N rank bucket

# ---------------------------------------------------------------------------
# Schema

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS aiem_sector_rotation (
    id               BIGSERIAL PRIMARY KEY,
    date             DATE              NOT NULL,
    sector_ticker    VARCHAR(10)       NOT NULL,
    sector_name      VARCHAR(60),
    spy_relative_1d  DOUBLE PRECISION,
    spy_relative_3d  DOUBLE PRECISION,
    spy_relative_5d  DOUBLE PRECISION,
    spy_relative_20d DOUBLE PRECISION,
    volume_ratio     DOUBLE PRECISION,
    rank_today       INTEGER,
    rank_yesterday   INTEGER,
    rank_change      INTEGER,
    tier             INTEGER,
    direction        VARCHAR(10),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, sector_ticker)
);

CREATE TABLE IF NOT EXISTS aiem_sector_alerts_log (
    id             BIGSERIAL PRIMARY KEY,
    date           DATE         NOT NULL,
    sector_ticker  VARCHAR(10)  NOT NULL,
    tier           INTEGER,
    direction      VARCHAR(10),
    message_sent   BOOLEAN      NOT NULL DEFAULT FALSE,
    details        JSONB,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

def init_schema(conn):
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL)
    conn.commit()
    log.info("[module7] schema ready (aiem_sector_rotation + aiem_sector_alerts_log)")


# ---------------------------------------------------------------------------
# Core computation

def _fetch_rs_history(cur, n_days: int = 65) -> list:
    """
    Fetch last n_days of OHLCV + rvol for all sector tickers + SPY.
    Returns list of dicts sorted by (ticker, scan_date).
    """
    cur.execute("""
        WITH ordered AS (
            SELECT
                ticker,
                scan_date,
                close_price,
                prev_close,
                rvol,
                LAG(close_price, 1)  OVER w AS close_1d_ago,
                LAG(close_price, 3)  OVER w AS close_3d_ago,
                LAG(close_price, 5)  OVER w AS close_5d_ago,
                LAG(close_price, 20) OVER w AS close_20d_ago,
                ROW_NUMBER()         OVER w AS rn_desc
            FROM polygon_market_daily
            WHERE ticker = ANY(%s)
            WINDOW w AS (PARTITION BY ticker ORDER BY scan_date)
        ),
        cut AS (
            SELECT *
            FROM ordered
            WHERE scan_date >= (
                SELECT scan_date
                FROM ordered
                WHERE ticker = 'SPY'
                ORDER BY scan_date DESC
                OFFSET %s - 1 LIMIT 1
            )
        )
        SELECT ticker, scan_date, close_price, rvol,
               close_1d_ago, close_3d_ago, close_5d_ago, close_20d_ago
        FROM cut
        ORDER BY ticker, scan_date
    """, (_ALL_TICKERS, n_days))
    cols = ["ticker","scan_date","close","rvol",
            "c1","c3","c5","c20"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _pct(close, ref):
    if ref and ref != 0 and close:
        return (close - ref) / ref * 100.0
    return None


def _compute_metrics(rows: list) -> dict:
    """
    From raw rows, compute per-sector RS metrics for the most recent date.
    Returns {ticker: {rs_1d, rs_3d, rs_5d, rs_20d, rvol, date}}.
    """
    # Organise into {ticker: [rows sorted by date]}
    by_ticker: dict = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    latest_date = max(r["scan_date"] for r in rows if r["ticker"] == "SPY")

    def _latest(ticker):
        for r in reversed(by_ticker.get(ticker, [])):
            if r["scan_date"] == latest_date:
                return r
        return None

    spy = _latest("SPY")
    if spy is None:
        raise RuntimeError("SPY row missing for latest date")

    spy_1d  = _pct(spy["close"], spy["c1"])
    spy_3d  = _pct(spy["close"], spy["c3"])
    spy_5d  = _pct(spy["close"], spy["c5"])
    spy_20d = _pct(spy["close"], spy["c20"])

    result = {}
    for tkr in _SECTOR_TICKERS:
        row = _latest(tkr)
        if row is None:
            continue
        etf_1d  = _pct(row["close"], row["c1"])
        etf_3d  = _pct(row["close"], row["c3"])
        etf_5d  = _pct(row["close"], row["c5"])
        etf_20d = _pct(row["close"], row["c20"])
        result[tkr] = {
            "date":    latest_date,
            "rs_1d":   round(etf_1d  - spy_1d,  4) if (etf_1d  is not None and spy_1d  is not None) else None,
            "rs_3d":   round(etf_3d  - spy_3d,  4) if (etf_3d  is not None and spy_3d  is not None) else None,
            "rs_5d":   round(etf_5d  - spy_5d,  4) if (etf_5d  is not None and spy_5d  is not None) else None,
            "rs_20d":  round(etf_20d - spy_20d, 4) if (etf_20d is not None and spy_20d is not None) else None,
            "rvol":    float(row["rvol"]) if row["rvol"] is not None else None,
        }
    return result


def _compute_60d_stdev(rows: list) -> dict:
    """
    Compute 60-day standard deviation of daily RS (sector - SPY 1d returns)
    for each sector ticker. Returns {ticker: stdev}.
    """
    import statistics

    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["scan_date"], {})[r["ticker"]] = r

    dates_sorted = sorted(by_date.keys())
    series: dict = {t: [] for t in _SECTOR_TICKERS}

    for date in dates_sorted:
        day = by_date[date]
        spy_row = day.get("SPY")
        if spy_row is None:
            continue
        spy_1d = _pct(spy_row["close"], spy_row["c1"])
        if spy_1d is None:
            continue
        for tkr in _SECTOR_TICKERS:
            etf_row = day.get(tkr)
            if etf_row is None:
                continue
            etf_1d = _pct(etf_row["close"], etf_row["c1"])
            if etf_1d is not None:
                series[tkr].append(etf_1d - spy_1d)

    stdev = {}
    for tkr, vals in series.items():
        if len(vals) >= 5:
            try:
                stdev[tkr] = statistics.stdev(vals)
            except Exception:
                stdev[tkr] = None
        else:
            stdev[tkr] = None
    return stdev


def _assign_ranks(metrics: dict, window: str) -> dict:
    """
    Assign ranks 1–12 by RS for a given window (rs_1d, rs_3d, rs_5d, rs_20d).
    Rank 1 = highest RS (strongest/most heating). Returns {ticker: rank}.
    """
    scored = [(tkr, m[window]) for tkr, m in metrics.items() if m.get(window) is not None]
    scored.sort(key=lambda x: x[1], reverse=True)
    return {tkr: i + 1 for i, (tkr, _) in enumerate(scored)}


def _fetch_recent_ranks(cur, n_days: int = 5) -> dict:
    """
    Fetch the last n_days of rank_today from aiem_sector_rotation.
    Returns {ticker: [oldest_rank, ..., yesterday_rank]} (excluding today).
    """
    cur.execute("""
        SELECT sector_ticker, date, rank_today
        FROM aiem_sector_rotation
        WHERE date >= (CURRENT_DATE - %s)
        ORDER BY sector_ticker, date DESC
    """, (n_days + 2,))
    result: dict = {}
    for tkr, date, rank in cur.fetchall():
        result.setdefault(tkr, []).append((date, rank))
    return result


def _fetch_recent_rvol(cur, n_days: int = 3) -> dict:
    """
    Fetch the last n_days of rvol from polygon_market_daily for sector ETFs.
    Returns {ticker: [rvol_oldest, ..., rvol_latest]}.
    """
    cur.execute("""
        SELECT ticker, scan_date, rvol
        FROM polygon_market_daily
        WHERE ticker = ANY(%s)
          AND scan_date >= (
              SELECT scan_date
              FROM polygon_market_daily
              WHERE ticker = 'SPY'
              ORDER BY scan_date DESC
              OFFSET %s - 1 LIMIT 1
          )
        ORDER BY ticker, scan_date DESC
    """, (_SECTOR_TICKERS, n_days))
    result: dict = {}
    for tkr, date, rvol in cur.fetchall():
        result.setdefault(tkr, []).append(float(rvol) if rvol is not None else None)
    return result


def _classify_tier(
    tkr: str,
    metrics: dict,
    stdev: dict,
    rank_1d: dict,
    recent_ranks: dict,     # {ticker: [(date, rank), ...] most-recent first}
    recent_rvol: dict,      # {ticker: [rvol most-recent first]}
    direction: str,         # 'heating' or 'cooling'
) -> Optional[int]:
    """
    Classify a sector into Tier 1/2/3 or None.
    direction: 'heating' = positive RS; 'cooling' = negative RS.
    """
    m = metrics.get(tkr, {})
    rs_1d  = m.get("rs_1d")
    rs_3d  = m.get("rs_3d")
    rs_5d  = m.get("rs_5d")
    rs_20d = m.get("rs_20d")
    rvol   = m.get("rvol")
    sign   = 1 if direction == "heating" else -1

    # ── Tier 3 check ─────────────────────────────────────────────────────────
    # Condition: RS positive (or negative) across 3d, 5d, 20d; rank in
    # top-3 or bottom-3 for 4+ consecutive trading days; rvol sustained.
    t3_rs = (
        rs_3d  is not None and sign * rs_3d  > 0 and
        rs_5d  is not None and sign * rs_5d  > 0 and
        rs_20d is not None and sign * rs_20d > 0
    )
    cur_rank = rank_1d.get(tkr)
    t3_rank_ok = cur_rank is not None and (
        cur_rank <= _T3_TOP_N if direction == "heating" else
        cur_rank >= (len(_SECTOR_TICKERS) - _T3_TOP_N + 1)
    )
    hist = recent_ranks.get(tkr, [])   # [(date, rank), ...] most-recent first
    consec = 0
    for _, r in hist:
        in_bucket = (r <= _T3_TOP_N if direction == "heating"
                     else r >= len(_SECTOR_TICKERS) - _T3_TOP_N + 1)
        if in_bucket:
            consec += 1
        else:
            break
    t3_consec_ok = consec >= _T3_CONSEC_RANK_DAYS - 1   # today counts as +1

    rvol_hist = recent_rvol.get(tkr, [])[:3]
    t3_vol_ok = sum(1 for v in rvol_hist if v is not None and v >= _T2_VOL_RATIO_MIN) >= _T2_MIN_DAYS if len(rvol_hist) >= 2 else False

    if t3_rs and t3_rank_ok and t3_consec_ok and t3_vol_ok:
        return 3

    # ── Tier 2 check ─────────────────────────────────────────────────────────
    # Condition: RS positive across 3d AND 5d; rvol > 1.2 on ≥2 of last 3 days.
    t2_rs = (
        rs_3d is not None and sign * rs_3d > 0 and
        rs_5d is not None and sign * rs_5d > 0
    )
    t2_vol_ok = sum(1 for v in rvol_hist if v is not None and v >= _T2_VOL_RATIO_MIN) >= _T2_VOL_MIN_DAYS
    if t2_rs and t2_vol_ok:
        return 2

    # ── Tier 1 check ─────────────────────────────────────────────────────────
    # Condition: 1d RS > 1.5 SD OR rank jump >= 4 positions.
    sd = stdev.get(tkr)
    t1_sd_ok = (
        rs_1d is not None and sd is not None and sd > 0 and
        abs(rs_1d) >= _T1_SD_MULTIPLIER * sd
    )
    yesterday_rank = hist[0][1] if hist else None
    rank_jump = abs(cur_rank - yesterday_rank) if (cur_rank and yesterday_rank) else 0
    t1_jump_ok = rank_jump >= _T1_RANK_JUMP

    if t1_sd_ok or t1_jump_ok:
        return 1

    return None


# Fix: reference the constant by name inside _classify_tier
_T2_MIN_DAYS = _T2_VOL_MIN_DAYS   # alias for use inside the function body above


# ---------------------------------------------------------------------------
# Main batch runner

def run_sector_rotation(conn) -> dict:
    """Compute and store daily sector rotation state for all 12 sector ETFs."""
    import time
    t0 = time.time()

    with conn.cursor() as cur:
        rows = _fetch_rs_history(cur, n_days=65)

    if not rows:
        return {"error": "no data in polygon_market_daily for sector tickers"}

    metrics = _compute_metrics(rows)
    stdev   = _compute_60d_stdev(rows)

    if not metrics:
        return {"error": "could not compute metrics (SPY row missing?)"}

    run_date = list(metrics.values())[0]["date"]
    log.info(f"[module7] computing sector rotation for {run_date} ({len(metrics)} sectors)")

    rank_1d  = _assign_ranks(metrics, "rs_1d")
    rank_3d  = _assign_ranks(metrics, "rs_3d")
    rank_5d  = _assign_ranks(metrics, "rs_5d")
    rank_20d = _assign_ranks(metrics, "rs_20d")

    with conn.cursor() as cur:
        recent_ranks = _fetch_recent_ranks(cur, n_days=6)
        recent_rvol  = _fetch_recent_rvol(cur, n_days=3)

    yesterday_ranks: dict = {}
    for tkr, hist in recent_ranks.items():
        if hist:
            yesterday_ranks[tkr] = hist[0][1]

    results = []
    alerts  = []

    for tkr in _SECTOR_TICKERS:
        m = metrics.get(tkr)
        if m is None:
            continue

        cur_rank = rank_1d.get(tkr)
        prev_rank = yesterday_ranks.get(tkr)
        rank_change = (prev_rank - cur_rank) if (cur_rank and prev_rank) else None

        rs_1d = m.get("rs_1d") or 0.0

        direction = "heating" if rs_1d >= 0 else "cooling"

        tier = _classify_tier(
            tkr, metrics, stdev, rank_1d, recent_ranks, recent_rvol, direction,
        )

        row = {
            "date":            run_date,
            "sector_ticker":   tkr,
            "sector_name":     _SECTORS.get(tkr, tkr),
            "spy_relative_1d": m.get("rs_1d"),
            "spy_relative_3d": m.get("rs_3d"),
            "spy_relative_5d": m.get("rs_5d"),
            "spy_relative_20d":m.get("rs_20d"),
            "volume_ratio":    m.get("rvol"),
            "rank_today":      cur_rank,
            "rank_yesterday":  prev_rank,
            "rank_change":     rank_change,
            "tier":            tier,
            "direction":       direction,
        }
        results.append(row)

        if tier and tier >= 2:
            alerts.append(row)

        log.debug(
            f"  {tkr:5s}  rank={cur_rank:2d}  rs_1d={m.get('rs_1d'):+.2f}%  "
            f"rs_5d={m.get('rs_5d'):+.2f}%  rvol={m.get('rvol'):.2f}x  "
            f"tier={tier}  dir={direction}"
        )

    with conn.cursor() as cur:
        for row in results:
            cur.execute("""
                INSERT INTO aiem_sector_rotation
                    (date, sector_ticker, sector_name,
                     spy_relative_1d, spy_relative_3d, spy_relative_5d, spy_relative_20d,
                     volume_ratio, rank_today, rank_yesterday, rank_change,
                     tier, direction)
                VALUES
                    (%(date)s, %(sector_ticker)s, %(sector_name)s,
                     %(spy_relative_1d)s, %(spy_relative_3d)s, %(spy_relative_5d)s, %(spy_relative_20d)s,
                     %(volume_ratio)s, %(rank_today)s, %(rank_yesterday)s, %(rank_change)s,
                     %(tier)s, %(direction)s)
                ON CONFLICT (date, sector_ticker) DO UPDATE SET
                    spy_relative_1d  = EXCLUDED.spy_relative_1d,
                    spy_relative_3d  = EXCLUDED.spy_relative_3d,
                    spy_relative_5d  = EXCLUDED.spy_relative_5d,
                    spy_relative_20d = EXCLUDED.spy_relative_20d,
                    volume_ratio     = EXCLUDED.volume_ratio,
                    rank_today       = EXCLUDED.rank_today,
                    rank_yesterday   = EXCLUDED.rank_yesterday,
                    rank_change      = EXCLUDED.rank_change,
                    tier             = EXCLUDED.tier,
                    direction        = EXCLUDED.direction
            """, row)

        for alert in alerts:
            cur.execute("""
                INSERT INTO aiem_sector_alerts_log
                    (date, sector_ticker, tier, direction, message_sent, details)
                VALUES (%s, %s, %s, %s, FALSE, %s)
                ON CONFLICT DO NOTHING
            """, (
                alert["date"], alert["sector_ticker"],
                alert["tier"], alert["direction"],
                None,
            ))

        conn.commit()

    elapsed = round(time.time() - t0, 1)
    log.info(f"[module7] done: {len(results)} sectors stored, {len(alerts)} tier-2+ alerts ({elapsed}s)")

    return {
        "run_date":      str(run_date),
        "sectors_stored":len(results),
        "tier2_alerts":  len([a for a in alerts if a["tier"] == 2]),
        "tier3_alerts":  len([a for a in alerts if a["tier"] == 3]),
        "elapsed_seconds": elapsed,
        "snapshot": sorted(results, key=lambda r: r["rank_today"] or 99),
    }


# ---------------------------------------------------------------------------
# Layer 10 integration: query current sector state by ETF ticker

def get_sector_state(conn, sector_etf: str) -> Optional[dict]:
    """
    Return the most recent aiem_sector_rotation row for a given sector ETF ticker.
    Layer 10 should call this to adjust conviction scores:
      Tier 3 heating  → up-weight bullish signals in this sector
      Tier 3 cooling  → down-weight bullish signals in this sector
    Returns None if no data available.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date, sector_ticker, sector_name, tier, direction,
                   spy_relative_1d, spy_relative_3d, spy_relative_5d, spy_relative_20d,
                   volume_ratio, rank_today
            FROM aiem_sector_rotation
            WHERE sector_ticker = %s
            ORDER BY date DESC
            LIMIT 1
        """, (sector_etf.upper(),))
        row = cur.fetchone()
    if row is None:
        return None
    cols = ["date","sector_ticker","sector_name","tier","direction",
            "rs_1d","rs_3d","rs_5d","rs_20d","volume_ratio","rank_today"]
    return {k: (str(v) if hasattr(v, 'isoformat') else v) for k, v in zip(cols, row)}


def get_all_tier3_sectors(conn) -> list:
    """
    Return all sectors currently at Tier 3 (most recent date).
    Convenience wrapper for Layer 10 bulk scoring.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (sector_ticker)
                sector_ticker, sector_name, tier, direction, date
            FROM aiem_sector_rotation
            WHERE tier = 3
            ORDER BY sector_ticker, date DESC
        """)
        rows = cur.fetchall()
    return [
        {"sector_ticker": r[0], "sector_name": r[1],
         "tier": r[2], "direction": r[3], "date": str(r[4])}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Status report for GET /aiem/module7-status

def get_module7_status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (sector_ticker)
                date, sector_ticker, sector_name,
                spy_relative_1d, spy_relative_3d, spy_relative_5d, spy_relative_20d,
                volume_ratio, rank_today, rank_change, tier, direction
            FROM aiem_sector_rotation
            ORDER BY sector_ticker, date DESC
        """)
        latest = cur.fetchall()

        cur.execute("SELECT MAX(date) FROM aiem_sector_rotation")
        last_run = cur.fetchone()[0]

        cur.execute("""
            SELECT tier, direction, COUNT(*) as n
            FROM aiem_sector_rotation
            WHERE date = (SELECT MAX(date) FROM aiem_sector_rotation)
              AND tier IS NOT NULL
            GROUP BY tier, direction ORDER BY tier, direction
        """)
        tier_summary = [{"tier": r[0], "direction": r[1], "count": r[2]}
                        for r in cur.fetchall()]

        cur.execute("""
            SELECT date, sector_ticker, tier, direction, message_sent
            FROM aiem_sector_alerts_log
            ORDER BY date DESC, tier DESC
            LIMIT 20
        """)
        alert_log = [{"date": str(r[0]), "ticker": r[1],
                      "tier": r[2], "direction": r[3], "sent": r[4]}
                     for r in cur.fetchall()]

    cols = ["date","sector_ticker","sector_name",
            "rs_1d","rs_3d","rs_5d","rs_20d",
            "volume_ratio","rank_today","rank_change","tier","direction"]

    snapshot = [
        {k: (str(v) if hasattr(v, 'isoformat') else v) for k, v in zip(cols, row)}
        for row in latest
    ]
    snapshot.sort(key=lambda r: r["rank_today"] or 99)

    return {
        "last_run_date": str(last_run) if last_run else None,
        "tier_summary":  tier_summary,
        "snapshot":      snapshot,
        "recent_alerts": alert_log,
    }
