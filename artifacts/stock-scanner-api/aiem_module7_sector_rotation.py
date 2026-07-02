"""
aiem_module7_sector_rotation.py — Module 7: Sector Rotation Detector

Daily scan of sector ETF data from polygon_market_daily to detect which sectors
are heating up or cooling off. Surfaces rotation as tiered-confidence signals:
  Tier 1 — Early move     (logged only, no alert)
  Tier 2 — Developing     (Telegram alert, watch)
  Tier 3 — Confirmed      (Telegram alert, feeds conviction weighting)

Addendum additions (July 2026):
  - Range breakout detection (20d / 60d highs): breakout_60d + rvol>1.3 = immediate Tier 3
  - Expanded universe: 6 international ETFs added (EUFN, EWJ, EEM, EWG, EWU, VGK)
  - Optional Tier 1 leading indicators: MACD cross, price > 50d MA (log only)

Data source: polygon_market_daily (19 tickers with 498 days — no API calls, ~1s runtime).
"""

import logging
import statistics
from typing import Optional

log = logging.getLogger("module7_sector_rotation")

# ---------------------------------------------------------------------------
# Universe (12 US sector ETFs + 6 international)

_SECTORS = {
    # US sector SPDRs
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
    # International / regional
    "EUFN": "Europe Financials",
    "EWJ":  "Japan",
    "EEM":  "Emerging Markets",
    "EWG":  "Germany",
    "EWU":  "United Kingdom",
    "VGK":  "Europe Broad",
}
_SECTOR_TICKERS = list(_SECTORS.keys())   # 18 tickers
_ALL_TICKERS    = ["SPY"] + _SECTOR_TICKERS

# ---------------------------------------------------------------------------
# Thresholds

_T1_SD_MULTIPLIER     = 1.5   # Tier 1 RS path: |1d RS| > 1.5 SD
_T1_RANK_JUMP         = 4     # Tier 1 RS path: rank jump >= 4 positions
_T2_VOL_RATIO_MIN     = 1.2   # Tier 2 RS path: rvol > 1.2x on ≥2 of last 3 days
_T2_VOL_MIN_DAYS      = 2
_T3_CONSEC_RANK_DAYS  = 4     # Tier 3 RS path: consecutive days in top/bottom bucket
_T3_TOP_N             = 3     # Tier 3 RS path: top/bottom N by rank
_T3_BREAKOUT_VOL_MIN  = 1.3   # Tier 3 breakout path: rvol > 1.3x on breakout day
_BREAKOUT_VOL_MIN     = _T3_BREAKOUT_VOL_MIN   # alias

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
    high_20d         DOUBLE PRECISION,
    high_60d         DOUBLE PRECISION,
    breakout_20d     BOOLEAN,
    breakout_60d     BOOLEAN,
    breakout_trigger BOOLEAN,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, sector_ticker)
);

-- Idempotent for pre-addendum installs
ALTER TABLE aiem_sector_rotation
    ADD COLUMN IF NOT EXISTS high_20d         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS high_60d         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS breakout_20d     BOOLEAN,
    ADD COLUMN IF NOT EXISTS breakout_60d     BOOLEAN,
    ADD COLUMN IF NOT EXISTS breakout_trigger BOOLEAN;

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
    log.info("[module7] schema ready (+ breakout columns, 18-ticker universe)")


# ---------------------------------------------------------------------------
# Data fetch — history + breakout highs in one query

def _fetch_history(cur, n_days: int = 65) -> list:
    """
    Fetch last n_days of OHLCV + rvol + rolling max (for breakout) for all tickers.
    Returns list of row dicts ordered by (ticker, scan_date).
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
                MAX(close_price)     OVER (PARTITION BY ticker ORDER BY scan_date
                                          ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS high_20d,
                MAX(close_price)     OVER (PARTITION BY ticker ORDER BY scan_date
                                          ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING) AS high_60d
            FROM polygon_market_daily
            WHERE ticker = ANY(%s)
            WINDOW w AS (PARTITION BY ticker ORDER BY scan_date)
        )
        SELECT ticker, scan_date, close_price, rvol,
               close_1d_ago, close_3d_ago, close_5d_ago, close_20d_ago,
               high_20d, high_60d
        FROM ordered
        WHERE scan_date >= (
            SELECT scan_date FROM ordered
            WHERE ticker = 'SPY'
            ORDER BY scan_date DESC
            OFFSET %s - 1 LIMIT 1
        )
        ORDER BY ticker, scan_date
    """, (_ALL_TICKERS, n_days))
    cols = ["ticker", "scan_date", "close", "rvol",
            "c1", "c3", "c5", "c20", "high_20d", "high_60d"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Technical indicator helpers

def _ema_series(closes: list, period: int) -> list:
    """Full EMA series over a list of closes."""
    if not closes:
        return []
    k = 2.0 / (period + 1)
    emas = [closes[0]]
    for c in closes[1:]:
        emas.append(c * k + emas[-1] * (1 - k))
    return emas


def _compute_ta_indicators(by_ticker: dict) -> dict:
    """
    Compute optional Tier 1 leading indicators (MACD cross, 50d MA cross)
    from the close price series for each sector ticker.
    Returns {ticker: {macd_cross: bool, ma50_cross: bool}}.
    These are Tier 1 early signals only — they do not independently trigger alerts.
    """
    result = {}
    for tkr in _SECTOR_TICKERS:
        rows = sorted(by_ticker.get(tkr, []), key=lambda r: r["scan_date"])
        closes = [r["close"] for r in rows if r["close"] is not None]
        if len(closes) < 52:
            result[tkr] = {"macd_cross": False, "ma50_cross": False}
            continue

        # 50-day MA cross (today vs yesterday)
        ma50_today = sum(closes[-50:]) / 50
        ma50_yest  = sum(closes[-51:-1]) / 50
        close_today = closes[-1]
        close_yest  = closes[-2]
        ma50_cross = (close_yest < ma50_yest) and (close_today > ma50_today)

        # MACD (12/26/9) cross
        macd_cross = False
        if len(closes) >= 35:
            ema12 = _ema_series(closes, 12)
            ema26 = _ema_series(closes, 26)
            macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
            if len(macd_line) >= 9:
                signal = _ema_series(macd_line, 9)
                if len(signal) >= 2:
                    macd_cross = (
                        macd_line[-2] < signal[-2] and
                        macd_line[-1] > signal[-1]
                    )

        result[tkr] = {"macd_cross": macd_cross, "ma50_cross": ma50_cross}
    return result


# ---------------------------------------------------------------------------
# Core metric computation

def _pct(close, ref):
    if ref and ref != 0 and close:
        return (close - ref) / ref * 100.0
    return None


def _compute_metrics(rows: list) -> dict:
    """
    Compute per-sector RS + breakout metrics for the most recent date.
    Returns {ticker: {rs_1d, rs_3d, rs_5d, rs_20d, rvol,
                      breakout_20d, breakout_60d, high_20d, high_60d, date}}.
    """
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

        h20 = float(row["high_20d"]) if row.get("high_20d") else None
        h60 = float(row["high_60d"]) if row.get("high_60d") else None
        cl  = float(row["close"])
        b20 = bool(cl > h20) if h20 else False
        b60 = bool(cl > h60) if h60 else False

        result[tkr] = {
            "date":         latest_date,
            "rs_1d":        round(etf_1d  - spy_1d,  4) if (etf_1d  is not None and spy_1d  is not None) else None,
            "rs_3d":        round(etf_3d  - spy_3d,  4) if (etf_3d  is not None and spy_3d  is not None) else None,
            "rs_5d":        round(etf_5d  - spy_5d,  4) if (etf_5d  is not None and spy_5d  is not None) else None,
            "rs_20d":       round(etf_20d - spy_20d, 4) if (etf_20d is not None and spy_20d is not None) else None,
            "rvol":         float(row["rvol"]) if row["rvol"] is not None else None,
            "high_20d":     h20,
            "high_60d":     h60,
            "breakout_20d": b20,
            "breakout_60d": b60,
        }

    return result, by_ticker


def _compute_60d_stdev(rows: list) -> dict:
    """Compute 60-day SD of daily RS (sector − SPY 1d returns) per ticker."""
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["scan_date"], {})[r["ticker"]] = r

    series: dict = {t: [] for t in _SECTOR_TICKERS}
    for date in sorted(by_date):
        day    = by_date[date]
        spy_r  = day.get("SPY")
        if spy_r is None:
            continue
        spy_1d = _pct(spy_r["close"], spy_r["c1"])
        if spy_1d is None:
            continue
        for tkr in _SECTOR_TICKERS:
            etf_r  = day.get(tkr)
            if etf_r is None:
                continue
            etf_1d = _pct(etf_r["close"], etf_r["c1"])
            if etf_1d is not None:
                series[tkr].append(etf_1d - spy_1d)

    stdev = {}
    for tkr, vals in series.items():
        try:
            stdev[tkr] = statistics.stdev(vals) if len(vals) >= 5 else None
        except Exception:
            stdev[tkr] = None
    return stdev


def _assign_ranks(metrics: dict, window: str) -> dict:
    """Rank all tracked tickers 1–N by RS for a given window (rank 1 = strongest)."""
    scored = [(t, m[window]) for t, m in metrics.items() if m.get(window) is not None]
    scored.sort(key=lambda x: x[1], reverse=True)
    return {t: i + 1 for i, (t, _) in enumerate(scored)}


def _fetch_recent_ranks(cur, n_days: int = 6) -> dict:
    """Return {ticker: [(date, rank), ...] most-recent first} from stored history."""
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
    """Return {ticker: [rvol most-recent first]} for last n_days from polygon_market_daily."""
    cur.execute("""
        SELECT ticker, scan_date, rvol
        FROM polygon_market_daily
        WHERE ticker = ANY(%s)
          AND scan_date >= (
              SELECT scan_date FROM polygon_market_daily
              WHERE ticker = 'SPY'
              ORDER BY scan_date DESC
              OFFSET %s - 1 LIMIT 1
          )
        ORDER BY ticker, scan_date DESC
    """, (_SECTOR_TICKERS, n_days))
    result: dict = {}
    for tkr, _date, rvol in cur.fetchall():
        result.setdefault(tkr, []).append(float(rvol) if rvol is not None else None)
    return result


# ---------------------------------------------------------------------------
# Tier classification

def _classify_tier(
    tkr: str,
    metrics: dict,
    stdev: dict,
    rank_1d: dict,
    recent_ranks: dict,
    recent_rvol: dict,
    ta_indicators: dict,
    direction: str,
) -> tuple:
    """
    Returns (tier: int|None, breakout_trigger: bool).
    breakout_trigger=True means Tier 3 was granted via the breakout path,
    not the sustained relative-strength path.
    """
    m       = metrics.get(tkr, {})
    rs_1d   = m.get("rs_1d")
    rs_3d   = m.get("rs_3d")
    rs_5d   = m.get("rs_5d")
    rs_20d  = m.get("rs_20d")
    rvol    = m.get("rvol") or 0.0
    b20     = m.get("breakout_20d", False)
    b60     = m.get("breakout_60d", False)
    sign    = 1 if direction == "heating" else -1
    n_total = len(_SECTOR_TICKERS)

    rvol_hist = recent_rvol.get(tkr, [])[:3]

    # ── Tier 3: breakout path (immediate — no sustained rank history needed) ─
    # 60-day range breakout on strong volume = confirmed signal.
    if b60 and rvol >= _BREAKOUT_VOL_MIN:
        return 3, True

    # ── Tier 3: sustained relative-strength path ─────────────────────────────
    t3_rs = (
        rs_3d  is not None and sign * rs_3d  > 0 and
        rs_5d  is not None and sign * rs_5d  > 0 and
        rs_20d is not None and sign * rs_20d > 0
    )
    cur_rank = rank_1d.get(tkr)
    t3_rank_ok = cur_rank is not None and (
        cur_rank <= _T3_TOP_N if direction == "heating"
        else cur_rank >= n_total - _T3_TOP_N + 1
    )
    hist   = recent_ranks.get(tkr, [])
    consec = 0
    for _, r in hist:
        in_bucket = (
            r <= _T3_TOP_N if direction == "heating"
            else r >= n_total - _T3_TOP_N + 1
        )
        if in_bucket:
            consec += 1
        else:
            break
    t3_consec_ok = consec >= _T3_CONSEC_RANK_DAYS - 1
    t3_vol_ok = (
        sum(1 for v in rvol_hist if v is not None and v >= _T2_VOL_RATIO_MIN)
        >= _T2_VOL_MIN_DAYS
        if len(rvol_hist) >= 2 else False
    )
    if t3_rs and t3_rank_ok and t3_consec_ok and t3_vol_ok:
        return 3, False

    # ── Tier 2: developing trend ──────────────────────────────────────────────
    # 20d-only breakout (no 60d confirmation or vol) = developing.
    if b20 and not b60:
        return 2, False

    t2_rs = (
        rs_3d is not None and sign * rs_3d > 0 and
        rs_5d is not None and sign * rs_5d > 0
    )
    t2_vol_ok = (
        sum(1 for v in rvol_hist if v is not None and v >= _T2_VOL_RATIO_MIN)
        >= _T2_VOL_MIN_DAYS
    )
    if t2_rs and t2_vol_ok:
        return 2, False

    # ── Tier 1: early move (log only, no alert) ───────────────────────────────
    sd = stdev.get(tkr)
    t1_rs_spike = (
        rs_1d is not None and sd is not None and sd > 0 and
        abs(rs_1d) >= _T1_SD_MULTIPLIER * sd
    )
    yesterday_rank = hist[0][1] if hist else None
    rank_jump = abs(cur_rank - yesterday_rank) if (cur_rank and yesterday_rank) else 0
    t1_jump = rank_jump >= _T1_RANK_JUMP

    ta = ta_indicators.get(tkr, {})
    t1_macd  = ta.get("macd_cross", False)
    t1_ma50  = ta.get("ma50_cross", False)

    if t1_rs_spike or t1_jump or t1_macd or t1_ma50:
        return 1, False

    return None, False


# ---------------------------------------------------------------------------
# Main batch runner

def run_sector_rotation(conn) -> dict:
    """Compute and store daily sector rotation state for all 18 ETFs."""
    import time
    t0 = time.time()

    with conn.cursor() as cur:
        rows = _fetch_history(cur, n_days=65)

    if not rows:
        return {"error": "no data in polygon_market_daily for sector tickers"}

    metrics, by_ticker = _compute_metrics(rows)
    stdev              = _compute_60d_stdev(rows)
    ta_indicators      = _compute_ta_indicators(by_ticker)

    if not metrics:
        return {"error": "could not compute metrics (SPY row missing?)"}

    run_date = list(metrics.values())[0]["date"]
    log.info(f"[module7] computing sector rotation for {run_date} ({len(metrics)} sectors)")

    rank_1d = _assign_ranks(metrics, "rs_1d")

    with conn.cursor() as cur:
        recent_ranks = _fetch_recent_ranks(cur, n_days=6)
        recent_rvol  = _fetch_recent_rvol(cur, n_days=3)

    yesterday_ranks = {
        tkr: hist[0][1]
        for tkr, hist in recent_ranks.items() if hist
    }

    results = []
    alerts  = []

    for tkr in _SECTOR_TICKERS:
        m = metrics.get(tkr)
        if m is None:
            continue

        cur_rank    = rank_1d.get(tkr)
        prev_rank   = yesterday_ranks.get(tkr)
        rank_change = (prev_rank - cur_rank) if (cur_rank and prev_rank) else None
        rs_1d       = m.get("rs_1d") or 0.0
        direction   = "heating" if rs_1d >= 0 else "cooling"

        tier, breakout_trigger = _classify_tier(
            tkr, metrics, stdev, rank_1d,
            recent_ranks, recent_rvol, ta_indicators, direction,
        )

        row = {
            "date":             run_date,
            "sector_ticker":    tkr,
            "sector_name":      _SECTORS.get(tkr, tkr),
            "spy_relative_1d":  m.get("rs_1d"),
            "spy_relative_3d":  m.get("rs_3d"),
            "spy_relative_5d":  m.get("rs_5d"),
            "spy_relative_20d": m.get("rs_20d"),
            "volume_ratio":     m.get("rvol"),
            "rank_today":       cur_rank,
            "rank_yesterday":   prev_rank,
            "rank_change":      rank_change,
            "tier":             tier,
            "direction":        direction,
            "high_20d":         m.get("high_20d"),
            "high_60d":         m.get("high_60d"),
            "breakout_20d":     m.get("breakout_20d", False),
            "breakout_60d":     m.get("breakout_60d", False),
            "breakout_trigger": breakout_trigger,
        }
        results.append(row)

        if tier and tier >= 2:
            alerts.append(row)

        ta = ta_indicators.get(tkr, {})
        log.debug(
            f"  {tkr:5s}  rank={cur_rank:2d}  rs_1d={m.get('rs_1d'):+.2f}%  "
            f"rs_5d={m.get('rs_5d') or 0:+.2f}%  rvol={m.get('rvol') or 0:.2f}x  "
            f"b20={m.get('breakout_20d')}  b60={m.get('breakout_60d')}  "
            f"macd={ta.get('macd_cross')}  ma50={ta.get('ma50_cross')}  "
            f"tier={tier}  bt={breakout_trigger}"
        )

    with conn.cursor() as cur:
        for row in results:
            cur.execute("""
                INSERT INTO aiem_sector_rotation
                    (date, sector_ticker, sector_name,
                     spy_relative_1d, spy_relative_3d, spy_relative_5d, spy_relative_20d,
                     volume_ratio, rank_today, rank_yesterday, rank_change,
                     tier, direction,
                     high_20d, high_60d, breakout_20d, breakout_60d, breakout_trigger)
                VALUES
                    (%(date)s, %(sector_ticker)s, %(sector_name)s,
                     %(spy_relative_1d)s, %(spy_relative_3d)s, %(spy_relative_5d)s, %(spy_relative_20d)s,
                     %(volume_ratio)s, %(rank_today)s, %(rank_yesterday)s, %(rank_change)s,
                     %(tier)s, %(direction)s,
                     %(high_20d)s, %(high_60d)s, %(breakout_20d)s, %(breakout_60d)s, %(breakout_trigger)s)
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
                    direction        = EXCLUDED.direction,
                    high_20d         = EXCLUDED.high_20d,
                    high_60d         = EXCLUDED.high_60d,
                    breakout_20d     = EXCLUDED.breakout_20d,
                    breakout_60d     = EXCLUDED.breakout_60d,
                    breakout_trigger = EXCLUDED.breakout_trigger
            """, row)

        for alert in alerts:
            cur.execute("""
                INSERT INTO aiem_sector_alerts_log
                    (date, sector_ticker, tier, direction, message_sent, details)
                VALUES (%s, %s, %s, %s, FALSE, %s::jsonb)
                ON CONFLICT DO NOTHING
            """, (
                alert["date"], alert["sector_ticker"],
                alert["tier"], alert["direction"],
                None,
            ))

        conn.commit()

    elapsed = round(time.time() - t0, 1)
    n2 = len([a for a in alerts if a["tier"] == 2])
    n3 = len([a for a in alerts if a["tier"] == 3])
    n3_bt = len([a for a in alerts if a["tier"] == 3 and a.get("breakout_trigger")])
    log.info(
        f"[module7] done: {len(results)} sectors, "
        f"tier-2={n2}, tier-3={n3} ({n3_bt} via breakout) ({elapsed}s)"
    )

    return {
        "run_date":               str(run_date),
        "sectors_stored":         len(results),
        "tier2_alerts":           n2,
        "tier3_alerts":           n3,
        "tier3_breakout_trigger": n3_bt,
        "elapsed_seconds":        elapsed,
        "snapshot":               sorted(results, key=lambda r: r["rank_today"] or 99),
    }


# ---------------------------------------------------------------------------
# Layer 10 integration

def get_sector_state(conn, sector_etf: str) -> Optional[dict]:
    """
    Most recent rotation row for a sector ETF ticker.
    Tier 3 heating  → up-weight bullish signals in this sector
    Tier 3 cooling  → down-weight bullish signals in this sector
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date, sector_ticker, sector_name, tier, direction,
                   spy_relative_1d, spy_relative_3d, spy_relative_5d, spy_relative_20d,
                   volume_ratio, rank_today, breakout_20d, breakout_60d, breakout_trigger
            FROM aiem_sector_rotation
            WHERE sector_ticker = %s
            ORDER BY date DESC LIMIT 1
        """, (sector_etf.upper(),))
        row = cur.fetchone()
    if row is None:
        return None
    cols = ["date","sector_ticker","sector_name","tier","direction",
            "rs_1d","rs_3d","rs_5d","rs_20d","volume_ratio","rank_today",
            "breakout_20d","breakout_60d","breakout_trigger"]
    return {k: (str(v) if hasattr(v, 'isoformat') else v) for k, v in zip(cols, row)}


def get_all_tier3_sectors(conn) -> list:
    """All sectors currently at Tier 3 (most recent stored date). For Layer 10."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (sector_ticker)
                sector_ticker, sector_name, tier, direction, date,
                breakout_trigger
            FROM aiem_sector_rotation
            WHERE tier = 3
            ORDER BY sector_ticker, date DESC
        """)
        rows = cur.fetchall()
    return [
        {"sector_ticker": r[0], "sector_name": r[1], "tier": r[2],
         "direction": r[3], "date": str(r[4]), "breakout_trigger": r[5]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Status report

def get_module7_status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (sector_ticker)
                date, sector_ticker, sector_name,
                spy_relative_1d, spy_relative_3d, spy_relative_5d, spy_relative_20d,
                volume_ratio, rank_today, rank_change, tier, direction,
                breakout_20d, breakout_60d, breakout_trigger
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
        tier_summary = [
            {"tier": r[0], "direction": r[1], "count": r[2]}
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT date, sector_ticker, tier, direction, message_sent
            FROM aiem_sector_alerts_log
            ORDER BY date DESC, tier DESC LIMIT 30
        """)
        alert_log = [
            {"date": str(r[0]), "ticker": r[1], "tier": r[2],
             "direction": r[3], "sent": r[4]}
            for r in cur.fetchall()
        ]

    cols = ["date","sector_ticker","sector_name",
            "rs_1d","rs_3d","rs_5d","rs_20d",
            "volume_ratio","rank_today","rank_change","tier","direction",
            "breakout_20d","breakout_60d","breakout_trigger"]
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
        "universe_size": len(_SECTOR_TICKERS),
    }
