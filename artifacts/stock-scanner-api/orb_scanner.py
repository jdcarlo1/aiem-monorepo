"""
orb_scanner.py — Live Opening Range Breakout (ORB) scanner.

Strategy (mirrors 2-year Pine Script backtest):
  - Opening Range:  9:30–9:59 AM ET (6 five-minute bars)
  - Entry signal:   First bar at/after 10:00 AM closing > ORB High × 1.003
  - Stops:          5% hard stop; 10% trailing from peak (managed by user)

Three validated patterns (backtest: 4,078 trades, 40 liquid tickers, 2yr):
  A: intraday RVOL ≥ 3×                 n=60   WR=65%  EV=+1.47%/trade
  B: intraday RVOL ≥ 3× + gap ≥ 1%     n=31   WR=68%  EV=+1.95%/trade
  C: intraday RVOL ≥ 3× + gap ≥ 2%     n=29   WR=66%  EV=+2.01%/trade

Intraday RVOL: (30-min opening volume) ÷ (avg_daily_vol × 0.20)
  — 0.20 = typical first-30-min share of NYSE/NASDAQ daily volume.
Today's gap: (9:30 first-bar open − yesterday's close) / yesterday's close × 100.

Data sources:
  - Polygon /v2/aggs/ticker/{t}/range/5/minute — today's 5-min bars
  - polygon_market_daily — 20d avg volume + yesterday's close
  - polygon_rvol_scan    — pre-screen universe (yesterday RVOL ≥ 2×)
"""
import os
import datetime as dt
import json
import time
import urllib.request
import urllib.parse

_POLYGON_API_KEY   = os.environ.get("POLYGON_API_KEY", "")
_BREAKOUT_MARGIN   = 1.003   # close must exceed ORB High by 0.3% to qualify
_FIRST_30MIN_SHARE = 0.20    # assumed fraction of daily vol in opening 30 min
_RATE_LIMIT_SEC    = 13.0    # Polygon Starter plan: 5 calls/min → 12s safe margin
_PRE_SCREEN_RVOL   = 2.0     # wide pre-screen using yesterday's RVOL from polygon_rvol_scan
_MAX_CANDIDATES    = 60      # cap to keep scan under 15 min at _RATE_LIMIT_SEC pace

_PATTERNS = {
    "A": {"rvol": 3.0, "gap": None},
    "B": {"rvol": 3.0, "gap": 1.0},
    "C": {"rvol": 3.0, "gap": 2.0},
}


# ── Polygon helpers ───────────────────────────────────────────────────────────

def _pg_get(url: str, params: dict) -> dict:
    """Polygon GET with 429 back-off (up to 3 attempts)."""
    import urllib.error
    all_params = {**params, "apiKey": _POLYGON_API_KEY}
    qs = urllib.parse.urlencode(all_params)
    for attempt in range(3):
        try:
            req = urllib.request.urlopen(f"{url}?{qs}", timeout=25)
            return json.loads(req.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = max(float(e.headers.get("Retry-After", 60)), 60.0)
                print(f"[orb_scanner] 429 — waiting {wait:.0f}s (attempt {attempt + 1})")
                time.sleep(wait)
            elif e.code in (403, 404):
                return {}   # no data for this ticker; not an error
            else:
                raise
    return {}


def _fetch_today_5min(ticker: str, today_str: str) -> list:
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
           f"/range/5/minute/{today_str}/{today_str}")
    data = _pg_get(url, {"adjusted": "true", "sort": "asc", "limit": "200"})
    return data.get("results") or []


def _to_et_bars(raw_bars: list) -> list:
    """Convert Polygon bars (ms UTC) to dicts with ET time annotation.
    EDT = UTC-4 (Apr–Nov); EST = UTC-5 (Nov–Mar). Using UTC-4 year-round is
    accurate enough for 9:30/10:00/15:55 bucketing."""
    UTC    = dt.timezone.utc
    ET_OFF = dt.timezone(dt.timedelta(hours=-4))
    out = []
    for b in raw_bars:
        et_dt = dt.datetime.fromtimestamp(b["t"] / 1000, tz=UTC).astimezone(ET_OFF)
        out.append({
            "et_time": et_dt.time(),
            "open":    float(b.get("o", 0)),
            "high":    float(b.get("h", 0)),
            "low":     float(b.get("l", 0)),
            "close":   float(b.get("c", 0)),
            "volume":  float(b.get("v", 0)),
        })
    return out


# ── ORB computation ───────────────────────────────────────────────────────────

def _compute_orb_metrics(et_bars: list, avg_daily_vol: float, prev_close: float):
    """
    Compute ORB metrics from today's 5-min ET bars.
    Returns dict or None if the 9:30–9:59 window has fewer than 3 bars
    (not enough data to define a meaningful opening range).
    """
    window = [
        b for b in et_bars
        if dt.time(9, 30) <= b["et_time"] < dt.time(10, 0)
    ]
    if len(window) < 3:
        return None

    orb_high  = max(b["high"]   for b in window)
    orb_low   = min(b["low"]    for b in window)
    orb_vol   = sum(b["volume"] for b in window)
    first_open = window[0]["open"]

    # Today's gap vs yesterday's close
    today_gap_pct = (
        round((first_open - prev_close) / prev_close * 100, 2)
        if prev_close and prev_close > 0 else None
    )

    # Intraday RVOL: 30-min vol vs expected 30-min vol (20% of avg daily)
    intraday_rvol = (
        round(orb_vol / (avg_daily_vol * _FIRST_30MIN_SHARE), 2)
        if avg_daily_vol > 0 else 0.0
    )

    return {
        "orb_high":      orb_high,
        "orb_low":       orb_low,
        "orb_vol":       orb_vol,
        "first_open":    first_open,
        "today_gap_pct": today_gap_pct,
        "intraday_rvol": intraday_rvol,
    }


def _detect_breakout(et_bars: list, orb_high: float):
    """Return the first bar at/after 10:00 AM with close > orb_high × 1.003."""
    target = orb_high * _BREAKOUT_MARGIN
    for b in et_bars:
        if b["et_time"] >= dt.time(10, 0) and b["close"] > target:
            return b
    return None


def _classify_patterns(intraday_rvol: float, today_gap_pct) -> list:
    """Return list of qualifying pattern keys, e.g. ['A', 'B', 'C']."""
    result = []
    for name, gates in _PATTERNS.items():
        if intraday_rvol < gates["rvol"]:
            continue
        if gates["gap"] is not None:
            if today_gap_pct is None or today_gap_pct < gates["gap"]:
                continue
        result.append(name)
    return result


# ── Main scanner ──────────────────────────────────────────────────────────────

def run_orb_scanner(db_url: str) -> dict:
    """
    Full scan pipeline:
      1. Pre-screen from polygon_rvol_scan (yesterday RVOL ≥ 2×); pull avg_vol_20d
         and prev_close in one SQL query.
      2. Fetch today's 5-min bars from Polygon for each candidate.
      3. Compute ORB High/Low, intraday RVOL, today's gap.
      4. Classify into patterns A/B/C; detect breakout.
      5. Upsert qualified hits to orb_signals.
    Returns summary dict.
    """
    import psycopg2
    import psycopg2.extras

    today = dt.date.today().isoformat()

    # ── Step 1: pre-screen + batch-load volume stats ──────────────────────────
    with psycopg2.connect(db_url, connect_timeout=10,
                          options="-c statement_timeout=20000") as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                WITH candidates AS (
                    SELECT p.ticker, p.rvol AS prev_rvol
                    FROM polygon_rvol_scan p
                    WHERE p.scan_date = (SELECT MAX(scan_date) FROM polygon_rvol_scan)
                      AND p.rvol  >= %s
                      AND p.price >= 2.0
                    ORDER BY p.rvol DESC
                    LIMIT %s
                ),
                vol_stats AS (
                    SELECT d.ticker,
                           AVG(d.volume::float) AS avg_vol_20d
                    FROM polygon_market_daily d
                    JOIN candidates c USING (ticker)
                    WHERE d.scan_date >= CURRENT_DATE - 30
                      AND d.scan_date <  CURRENT_DATE
                    GROUP BY d.ticker
                ),
                prev_closes AS (
                    SELECT DISTINCT ON (d.ticker)
                           d.ticker,
                           d.close_price AS prev_close
                    FROM polygon_market_daily d
                    JOIN candidates c USING (ticker)
                    WHERE d.scan_date < CURRENT_DATE
                    ORDER BY d.ticker, d.scan_date DESC
                )
                SELECT c.ticker, c.prev_rvol,
                       COALESCE(v.avg_vol_20d, 0) AS avg_vol_20d,
                       p.prev_close
                FROM candidates c
                LEFT JOIN vol_stats   v USING (ticker)
                LEFT JOIN prev_closes p USING (ticker)
            """, (_PRE_SCREEN_RVOL, _MAX_CANDIDATES))
            candidates = cur.fetchall()

            cur.execute("""
                SELECT ticker FROM orb_signals
                WHERE scan_date = CURRENT_DATE
            """)
            already_fired = {r["ticker"] for r in cur.fetchall()}

    print(f"[orb_scanner] {today}: {len(candidates)} candidates, "
          f"{len(already_fired)} already in orb_signals today")

    if not candidates:
        return {"date": today, "pre_screen": 0, "hits": 0, "new": 0, "results": []}

    # ── Step 2–4: fetch intraday bars, compute metrics, classify ─────────────
    hits = []

    for row in candidates:
        ticker     = row["ticker"]
        avg_vol    = float(row["avg_vol_20d"] or 0)
        prev_close = float(row["prev_close"]) if row["prev_close"] else None

        if avg_vol <= 0 or prev_close is None:
            continue

        time.sleep(_RATE_LIMIT_SEC)
        try:
            raw = _fetch_today_5min(ticker, today)
        except Exception as e:
            print(f"[orb_scanner] {ticker}: fetch error — {e}")
            continue

        et_bars = _to_et_bars(raw)
        if not et_bars:
            continue

        metrics = _compute_orb_metrics(et_bars, avg_vol, prev_close)
        if metrics is None:
            continue

        patterns = _classify_patterns(metrics["intraday_rvol"], metrics["today_gap_pct"])
        if not patterns:
            continue

        breakout_bar  = _detect_breakout(et_bars, metrics["orb_high"])
        current_price = et_bars[-1]["close"]

        hit = {
            "ticker":            ticker,
            "orb_high":          metrics["orb_high"],
            "orb_low":           metrics["orb_low"],
            "intraday_rvol":     metrics["intraday_rvol"],
            "today_gap_pct":     metrics["today_gap_pct"],
            "patterns":          patterns,
            "breakout_detected": breakout_bar is not None,
            "breakout_price":    breakout_bar["close"]        if breakout_bar else None,
            "breakout_time":     str(breakout_bar["et_time"]) if breakout_bar else None,
            "current_price":     current_price,
        }
        hits.append(hit)

        print(f"[orb_scanner] ✓ {ticker}: RVOL={metrics['intraday_rvol']:.1f}×  "
              f"gap={metrics['today_gap_pct']}%  patterns={patterns}  "
              f"breakout={'YES @ $' + str(round(breakout_bar['close'], 2)) if breakout_bar else 'pending'}")

    # ── Step 5: persist ───────────────────────────────────────────────────────
    new_hits = [h for h in hits if h["ticker"] not in already_fired]

    if new_hits:
        with psycopg2.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                for h in new_hits:
                    cur.execute("""
                        INSERT INTO orb_signals
                          (scan_date, ticker, orb_high, orb_low, intraday_rvol,
                           today_gap_pct, patterns, breakout_detected,
                           breakout_price, breakout_time, current_price)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (ticker, scan_date) DO UPDATE SET
                          intraday_rvol     = EXCLUDED.intraday_rvol,
                          breakout_detected = EXCLUDED.breakout_detected,
                          breakout_price    = EXCLUDED.breakout_price,
                          breakout_time     = EXCLUDED.breakout_time,
                          current_price     = EXCLUDED.current_price,
                          patterns          = EXCLUDED.patterns,
                          updated_at        = NOW()
                    """, (
                        today, h["ticker"],
                        h["orb_high"],         h["orb_low"],
                        h["intraday_rvol"],    h["today_gap_pct"],
                        h["patterns"],         # list → psycopg2 adapts to TEXT[]
                        h["breakout_detected"], h["breakout_price"],
                        h["breakout_time"],    h["current_price"],
                    ))
                conn.commit()

    print(f"[orb_scanner] {today}: {len(hits)} qualified, {len(new_hits)} new → DB")
    return {
        "date":       today,
        "pre_screen": len(candidates),
        "hits":       len(hits),
        "new":        len(new_hits),
        "results":    hits,
    }


def get_orb_signals(db_url: str) -> list:
    """Return today's ORB signals from orb_signals table, sorted by RVOL desc."""
    import psycopg2
    import psycopg2.extras
    try:
        with psycopg2.connect(db_url, connect_timeout=5,
                              options="-c statement_timeout=3000") as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT ticker, scan_date::text, orb_high, orb_low,
                           intraday_rvol, today_gap_pct, patterns,
                           breakout_detected, breakout_price,
                           breakout_time::text, current_price,
                           updated_at::text
                    FROM orb_signals
                    WHERE scan_date = CURRENT_DATE
                    ORDER BY intraday_rvol DESC NULLS LAST
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[orb_scanner] get_orb_signals error: {e}")
        return []
