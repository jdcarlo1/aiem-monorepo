"""
aiem_firstcandle.py  —  First-candle (9:30-9:35 ET) capture for AIEM.

Captures the first 5-minute candle for every morning gap-up stock, then
fills the day outcome at EOD.  Gives AIEM the intraday signal data it
needs to discover 70%+ same-morning explosion patterns over time.

Zero dependency on main.py / Flask / website backend.
Uses Tradier timesales for intraday bars and batch quotes for EOD close.

Public API (called from aiem_process.py):
  init_firstcandle_table(db_url)
  run_firstcandle_capture(db_url)       # 9:36 AM ET — first candle just closed
  run_firstcandle_outcome_fill(db_url)  # 4:45 PM ET — day settled
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date

import pytz
import requests

ET  = pytz.timezone("America/New_York")
_LOG = "[aiem_firstcandle]"


# ── Tradier auth ──────────────────────────────────────────────────────────────

def _td_token() -> str:
    return (os.environ.get("TRADIER_API_TOKEN_2") or
            os.environ.get("TRADIER_API_TOKEN") or "")

def _td_headers() -> dict:
    tok = _td_token()
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"} if tok else {}


# ── Tradier helpers ───────────────────────────────────────────────────────────

def _td_quotes_batch(symbols: list) -> dict:
    """Batch real-time/EOD quotes.  Returns {SYM: {last, open, prevclose}}."""
    hdrs = _td_headers()
    if not hdrs or not symbols:
        return {}
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/quotes",
            params={"symbols": ",".join(symbols[:200])},
            headers=hdrs, timeout=8,
        )
        if r.status_code != 200:
            return {}
        raw = r.json().get("quotes", {}).get("quote", [])
        if isinstance(raw, dict):
            raw = [raw]
        return {
            q["symbol"]: {
                "last":      float(q.get("last") or 0),
                "open":      float(q.get("open") or 0),
                "prevclose": float(q.get("prevclose") or 0),
            }
            for q in raw if q.get("symbol")
        }
    except Exception as exc:
        print(f"{_LOG} td_quotes error: {exc}")
        return {}


def _td_first_candle(ticker: str, trade_date: date) -> dict | None:
    """
    Fetch the 9:30-9:35 ET 5-minute candle from Tradier timesales.
    Returns dict {open, high, low, close, volume} or None on failure.
    Honestly returns None when no bar exists — never fabricates.
    """
    hdrs = _td_headers()
    if not hdrs:
        return None

    date_str  = trade_date.strftime("%Y-%m-%d")
    start_str = f"{date_str} 09:30:00"
    end_str   = f"{date_str} 09:36:00"

    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/timesales",
            params={
                "symbol":         ticker.upper(),
                "interval":       "5min",
                "start":          start_str,
                "end":            end_str,
                "session_filter": "open",
            },
            headers=hdrs, timeout=10,
        )
        if r.status_code != 200:
            return None

        series = r.json().get("series")
        if not series:
            return None
        data = series.get("data") or []
        if isinstance(data, dict):
            data = [data]

        for bar in data:
            t = str(bar.get("time") or "")
            if "09:30" in t:
                o = float(bar.get("open")   or 0)
                h = float(bar.get("high")   or 0)
                l = float(bar.get("low")    or 0)
                c = float(bar.get("close")  or 0)
                v = int(bar.get("volume")   or 0)
                if not o:
                    return None
                return {"open": o, "high": h, "low": l, "close": c, "volume": v}

        return None
    except Exception as exc:
        print(f"{_LOG} timesales {ticker}: {exc}")
        return None


# ── Universe ──────────────────────────────────────────────────────────────────

def _get_morning_universe(db_url: str, trade_date: date) -> list:
    """
    Build the capture universe from two sources:
      1. polygon_rvol_scan  — gap >= 2%, rvol >= 2x, price $2-$150 (top 80)
      2. aiem_independent_picks — today's stock picks

    Also joins polygon_market_daily for prior-day close_strength / rvol.
    Returns list of dicts, capped at 100 tickers to manage API rate limits.
    """
    import psycopg2

    date_str = trade_date.strftime("%Y-%m-%d")
    results  = {}

    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:

            # Source 1: polygon_rvol_scan — premarket gap + RVOL universe
            cur.execute("""
                SELECT p.ticker,
                       p.gap_pct        AS gap,
                       p.rvol           AS rvol,
                       m.close_strength AS prior_cs,
                       m.rvol           AS prior_rvol
                FROM polygon_rvol_scan p
                LEFT JOIN LATERAL (
                    SELECT close_strength, rvol
                    FROM polygon_market_daily
                    WHERE ticker   = p.ticker
                      AND scan_date < %s::date
                    ORDER BY scan_date DESC
                    LIMIT 1
                ) m ON true
                WHERE p.scan_date    = %s::date
                  AND p.gap_pct     >= 2.0
                  AND p.rvol        >= 2.0
                  AND p.close_price BETWEEN 2 AND 150
                ORDER BY p.rvol DESC
                LIMIT 80
            """, (date_str, date_str))

            for row in cur.fetchall():
                ticker, gap, rvol, prior_cs, prior_rvol = row
                results[ticker] = {
                    "ticker":               ticker,
                    "premarket_gap_pct":    float(gap or 0),
                    "premarket_rvol":       float(rvol or 0),
                    "prior_close_strength": float(prior_cs)   if prior_cs   is not None else None,
                    "prior_rvol":           float(prior_rvol) if prior_rvol is not None else None,
                }

            # Source 2: today's independent stock picks (may overlap with above)
            try:
                cur.execute("""
                    SELECT p.ticker,
                           m.close_strength AS prior_cs,
                           m.rvol           AS prior_rvol
                    FROM aiem_independent_picks p
                    LEFT JOIN LATERAL (
                        SELECT close_strength, rvol
                        FROM polygon_market_daily
                        WHERE ticker    = p.ticker
                          AND scan_date < %s::date
                        ORDER BY scan_date DESC
                        LIMIT 1
                    ) m ON true
                    WHERE p.pick_date = %s::date
                      AND p.pick_type = 'stock'
                """, (date_str, date_str))
                for row in cur.fetchall():
                    ticker, prior_cs, prior_rvol = row
                    if ticker not in results:
                        results[ticker] = {
                            "ticker":               ticker,
                            "premarket_gap_pct":    None,
                            "premarket_rvol":       None,
                            "prior_close_strength": float(prior_cs)   if prior_cs   is not None else None,
                            "prior_rvol":           float(prior_rvol) if prior_rvol is not None else None,
                        }
            except Exception:
                pass  # aiem_independent_picks may not exist yet

    except Exception as exc:
        print(f"{_LOG} universe query error: {exc}")
        return []

    universe = list(results.values())[:100]
    print(f"{_LOG} universe: {len(universe)} tickers for {date_str}")
    return universe


# ── DB table ──────────────────────────────────────────────────────────────────

def init_firstcandle_table(db_url: str) -> None:
    """Create aiem_first_candle_data if it doesn't exist (idempotent)."""
    import psycopg2
    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_first_candle_data (
                    id                     SERIAL PRIMARY KEY,
                    scan_date              DATE        NOT NULL,
                    ticker                 TEXT        NOT NULL,
                    premarket_gap_pct      NUMERIC,
                    premarket_rvol         NUMERIC,
                    prior_close_strength   NUMERIC,
                    prior_rvol             NUMERIC,
                    open_price             NUMERIC,
                    first_candle_high      NUMERIC,
                    first_candle_low       NUMERIC,
                    first_candle_close     NUMERIC,
                    first_candle_volume    BIGINT,
                    gap_held               BOOLEAN,
                    first_candle_direction TEXT,
                    first_candle_range_pct NUMERIC,
                    day_close              NUMERIC,
                    day_return_pct         NUMERIC,
                    day_win                BOOLEAN,
                    captured_at            TIMESTAMPTZ DEFAULT NOW(),
                    outcome_filled_at      TIMESTAMPTZ,
                    UNIQUE (scan_date, ticker)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_aiem_firstcandle_date
                ON aiem_first_candle_data (scan_date)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_aiem_firstcandle_signals
                ON aiem_first_candle_data (scan_date, gap_held, day_win)
            """)
        print(f"{_LOG} aiem_first_candle_data table ready")
    except Exception as exc:
        print(f"{_LOG} table init error: {exc}")


# ── Capture job (9:36 AM ET) ──────────────────────────────────────────────────

def run_firstcandle_capture(db_url: str) -> None:
    """
    Captures the 9:30-9:35 ET first candle for every morning gap-up stock.
    Scheduled at 9:36 AM ET Mon-Fri (first candle has just finished).

    Stores per-ticker:
      - premarket context (gap, rvol, prior close_strength)
      - first candle OHLCV
      - gap_held (close > open), direction, range_pct
    Day outcome (day_close / day_win) is filled later by run_firstcandle_outcome_fill.
    """
    import psycopg2

    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        print(f"{_LOG} capture skipped (weekend)")
        return

    trade_date = now_et.date()
    universe   = _get_morning_universe(db_url, trade_date)
    if not universe:
        print(f"{_LOG} capture: empty universe for {trade_date} — skipping")
        return

    print(f"{_LOG} capturing first candle for {len(universe)} tickers on {trade_date}…")

    def _capture_one(entry: dict) -> dict | None:
        ticker = entry["ticker"]
        candle = _td_first_candle(ticker, trade_date)
        if not candle or not candle.get("open"):
            return None

        o = candle["open"]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]
        v = candle["volume"]

        direction = ("up"   if (c - o) >  0.001 * o else
                     "down" if (o - c) >  0.001 * o else "flat")
        rng_pct   = round((h - l) / o * 100, 3) if o else None

        return {
            **entry,
            "open_price":             round(o, 4),
            "first_candle_high":      round(h, 4),
            "first_candle_low":       round(l, 4),
            "first_candle_close":     round(c, 4),
            "first_candle_volume":    v,
            "gap_held":               c > o,
            "first_candle_direction": direction,
            "first_candle_range_pct": rng_pct,
        }

    captured = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_capture_one, e): e for e in universe}
        try:
            for fut in as_completed(futs, timeout=120):
                try:
                    r = fut.result()
                    if r:
                        captured.append(r)
                except Exception as exc:
                    print(f"{_LOG} worker error: {exc}")
        except TimeoutError:
            print(f"{_LOG} capture: 120s timeout — {len(captured)} partial results kept")

    if not captured:
        print(f"{_LOG} capture: 0 candles fetched for {trade_date}")
        return

    date_str = trade_date.strftime("%Y-%m-%d")
    written  = 0
    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            for row in captured:
                cur.execute("""
                    INSERT INTO aiem_first_candle_data
                        (scan_date, ticker, premarket_gap_pct, premarket_rvol,
                         prior_close_strength, prior_rvol,
                         open_price, first_candle_high, first_candle_low,
                         first_candle_close, first_candle_volume,
                         gap_held, first_candle_direction, first_candle_range_pct)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (scan_date, ticker) DO NOTHING
                """, (
                    date_str,
                    row["ticker"],
                    row.get("premarket_gap_pct"),
                    row.get("premarket_rvol"),
                    row.get("prior_close_strength"),
                    row.get("prior_rvol"),
                    row["open_price"],
                    row["first_candle_high"],
                    row["first_candle_low"],
                    row["first_candle_close"],
                    row["first_candle_volume"],
                    row["gap_held"],
                    row["first_candle_direction"],
                    row["first_candle_range_pct"],
                ))
            written = len(captured)
    except Exception as exc:
        print(f"{_LOG} DB write error: {exc}")

    print(f"{_LOG} capture done — {written} first-candle rows written for {date_str}")


# ── Outcome fill job (4:45 PM ET) ─────────────────────────────────────────────

def run_firstcandle_outcome_fill(db_url: str) -> None:
    """
    Fills day_close, day_return_pct, day_win for today's captured rows.
    Scheduled at 4:45 PM ET (after market close, prices settled).

    day_win = True  → stock closed above its 9:30 open (profitable long)
    day_win = False → stock closed below its 9:30 open
    """
    import psycopg2

    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        print(f"{_LOG} outcome fill skipped (weekend)")
        return

    trade_date = now_et.date()
    date_str   = trade_date.strftime("%Y-%m-%d")

    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, open_price
                FROM aiem_first_candle_data
                WHERE scan_date  = %s
                  AND day_close  IS NULL
                  AND open_price IS NOT NULL
            """, (date_str,))
            rows = cur.fetchall()
    except Exception as exc:
        print(f"{_LOG} outcome fill query error: {exc}")
        return

    if not rows:
        print(f"{_LOG} outcome fill: no open rows for {date_str}")
        return

    tickers  = [r[0] for r in rows]
    open_map = {r[0]: float(r[1]) for r in rows}

    print(f"{_LOG} filling outcomes for {len(tickers)} tickers on {date_str}…")

    quotes = _td_quotes_batch(tickers)
    if not quotes:
        print(f"{_LOG} outcome fill: no quotes returned from Tradier")
        return

    updated = 0
    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
            for ticker, q in quotes.items():
                day_close = q.get("last")
                open_px   = open_map.get(ticker)
                if not day_close or not open_px:
                    continue
                ret_pct = round((day_close - open_px) / open_px * 100, 4)
                cur.execute("""
                    UPDATE aiem_first_candle_data
                    SET day_close         = %s,
                        day_return_pct    = %s,
                        day_win           = %s,
                        outcome_filled_at = NOW()
                    WHERE scan_date = %s AND ticker = %s
                """, (day_close, ret_pct, day_close > open_px, date_str, ticker))
                updated += 1
    except Exception as exc:
        print(f"{_LOG} outcome fill DB error: {exc}")

    print(f"{_LOG} outcome fill done — {updated} rows updated for {date_str}")
