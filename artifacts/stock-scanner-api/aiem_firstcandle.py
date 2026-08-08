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


from aiem_broker.tradier_config import TRADIER_API_BASE

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

def _polygon_key() -> str:
    return os.environ.get("POLYGON_API_KEY", "")


# ── Tradier helpers ───────────────────────────────────────────────────────────

def _td_premarket_hl(ticker: str, trade_date: date) -> dict:
    """
    Fetch pre-market session (4 AM–9:30 AM ET) high/low/volume from Tradier.
    Returns dict with premarket_high, premarket_low, premarket_volume or {}.
    """
    hdrs = _td_headers()
    if not hdrs:
        return {}
    date_str = trade_date.strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{TRADIER_API_BASE}/v1/markets/timesales",
            params={
                "symbol":         ticker.upper(),
                "interval":       "5min",
                "start":          f"{date_str} 04:00:00",
                "end":            f"{date_str} 09:29:59",
                "session_filter": "all",
            },
            headers=hdrs, timeout=8,
        )
        if r.status_code != 200:
            return {}
        series = r.json().get("series")
        if not series:
            return {}
        data = series.get("data") or []
        if isinstance(data, dict):
            data = [data]
        highs = [float(b["high"])   for b in data if b.get("high")]
        lows  = [float(b["low"])    for b in data if b.get("low")]
        vols  = [int(b.get("volume") or 0) for b in data]
        if not highs:
            return {}
        return {
            "premarket_high":   round(max(highs), 4),
            "premarket_low":    round(min(lows),  4),
            "premarket_volume": sum(vols),
        }
    except Exception as exc:
        print(f"{_LOG} premarket_hl {ticker}: {exc}")
        return {}


def _polygon_news_check(ticker: str, trade_date: date) -> dict:
    """
    Check Polygon for news published in the last 2 days on this ticker.
    Returns {has_news: bool|None, news_count: int}.
    """
    import urllib.request
    import json as _json
    from datetime import timedelta
    key = _polygon_key()
    if not key:
        return {"has_news": None, "news_count": 0}
    since = (trade_date - timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        url = (
            f"https://api.polygon.io/v2/reference/news"
            f"?ticker={ticker}&published_utc.gte={since}&limit=10&apiKey={key}"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read())
        results = data.get("results", [])
        return {"has_news": len(results) > 0, "news_count": len(results)}
    except Exception:
        return {"has_news": None, "news_count": 0}


def _polygon_intraday_signals(ticker: str, trade_date: date) -> dict:
    """
    Compute three pro-grade intraday signals from Tradier 1-minute bars
    for the 9:30-9:35 AM ET window (5 bars).

    Polygon's /v3/trades endpoint requires a higher subscription tier, so we
    use Tradier 1-min timesales as the data source — same Tradier token already
    used for the first-candle bar.  The bar-direction rule is a standard
    approximation when individual tick data is unavailable:

      1. CVD (Cumulative Volume Delta) — bar-direction rule:
           close > open → classify entire bar as buyer-initiated  (+volume)
           close < open → classify entire bar as seller-initiated (-volume)
           close == open → carry forward previous bar's direction

      2. VWAP — sum(typical_price × volume) / sum(volume)
           typical_price = (high + low + close) / 3

      3. Volume Profile — bucket typical prices to $0.05 levels;
           POC = highest-volume bucket;
           Value Area High/Low = price range covering 70% of volume

    Returns dict with: cum_delta, delta_pct, buy_vol, sell_vol, tick_count
                       (= bar count), vwap, vwap_vs_open_pct (None — filled
                       by caller), poc_price, poc_vs_open_pct (None), 
                       value_area_high, value_area_low
    or {} on failure/no data.
    """
    hdrs = _td_headers()
    if not hdrs:
        return {}

    date_str = trade_date.strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{TRADIER_API_BASE}/v1/markets/timesales",
            params={
                "symbol":         ticker.upper(),
                "interval":       "1min",
                "start":          f"{date_str} 09:30:00",
                "end":            f"{date_str} 09:35:00",
                "session_filter": "open",
            },
            headers=hdrs, timeout=10,
        )
        if r.status_code != 200:
            return {}
        series = r.json().get("series")
        if not series:
            return {}
        bars = series.get("data") or []
        if isinstance(bars, dict):
            bars = [bars]
    except Exception as exc:
        print(f"{_LOG} intraday_signals {ticker}: {exc}")
        return {}

    if not bars:
        return {}

    # ── Single-pass: CVD + VWAP + Volume Profile ──────────────────────────────
    buy_vol   = 0
    sell_vol  = 0
    sum_pv    = 0.0
    sum_v     = 0
    profile   = {}     # price_bucket → volume
    direction = 0      # +1 = buy bar, -1 = sell bar

    for bar in bars:
        try:
            o = float(bar.get("open")   or 0)
            h = float(bar.get("high")   or 0)
            l = float(bar.get("low")    or 0)
            c = float(bar.get("close")  or 0)
            v = int(bar.get("volume")   or 0)
        except (TypeError, ValueError):
            continue
        if not o or not c or v <= 0:
            continue

        # Bar-direction CVD rule
        if c > o:
            direction = 1
        elif c < o:
            direction = -1
        if direction == 1:
            buy_vol += v
        elif direction == -1:
            sell_vol += v

        # VWAP using typical price
        tp      = (h + l + c) / 3
        sum_pv += tp * v
        sum_v  += v

        # Volume Profile — bucket typical price to nearest $0.05
        bucket = round(round(tp / 0.05) * 0.05, 2)
        profile[bucket] = profile.get(bucket, 0) + v

    if sum_v == 0:
        return {}

    total_vol = buy_vol + sell_vol
    cum_delta = buy_vol - sell_vol
    delta_pct = round(cum_delta / total_vol * 100, 2) if total_vol > 0 else 0.0
    vwap      = round(sum_pv / sum_v, 4)

    # ── Volume Profile: POC + Value Area (70%) ────────────────────────────────
    sorted_buckets    = sorted(profile.items())
    total_profile_vol = sum(v for _, v in sorted_buckets)
    poc_idx   = max(range(len(sorted_buckets)), key=lambda i: sorted_buckets[i][1])
    poc_price = sorted_buckets[poc_idx][0]

    va_vol = sorted_buckets[poc_idx][1]
    lo_idx = hi_idx = poc_idx
    target = total_profile_vol * 0.70

    while va_vol < target:
        can_up = hi_idx + 1 < len(sorted_buckets)
        can_dn = lo_idx - 1 >= 0
        if not can_up and not can_dn:
            break
        if not can_up:
            lo_idx -= 1; va_vol += sorted_buckets[lo_idx][1]
        elif not can_dn:
            hi_idx += 1; va_vol += sorted_buckets[hi_idx][1]
        else:
            up_vol = sorted_buckets[hi_idx + 1][1]
            dn_vol = sorted_buckets[lo_idx - 1][1]
            if up_vol >= dn_vol:
                hi_idx += 1; va_vol += sorted_buckets[hi_idx][1]
            else:
                lo_idx -= 1; va_vol += sorted_buckets[lo_idx][1]

    return {
        "cum_delta":        cum_delta,
        "delta_pct":        delta_pct,
        "buy_vol":          buy_vol,
        "sell_vol":         sell_vol,
        "tick_count":       len(bars),
        "vwap":             vwap,
        "vwap_vs_open_pct": None,  # filled in _capture_one where open price is known
        "poc_price":        poc_price,
        "poc_vs_open_pct":  None,  # filled in _capture_one
        "value_area_high":  sorted_buckets[hi_idx][0],
        "value_area_low":   sorted_buckets[lo_idx][0],
    }


def _polygon_nbbo_spread(ticker: str) -> dict:
    """
    Fetch current bid/ask from Tradier quotes and return the spread.
    (Polygon's NBBO endpoints require a higher subscription tier;
     Tradier quotes provide bid/ask in the same API call we already use.)
    Returns {bid, ask, spread_pct} or {} on failure.
    """
    hdrs = _td_headers()
    if not hdrs:
        return {}
    try:
        r = requests.get(
            f"{TRADIER_API_BASE}/v1/markets/quotes",
            params={"symbols": ticker.upper()},
            headers=hdrs, timeout=6,
        )
        if r.status_code != 200:
            return {}
        raw = r.json().get("quotes", {}).get("quote", {})
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        bid = float(raw.get("bid") or 0)
        ask = float(raw.get("ask") or 0)
        if bid <= 0 or ask <= 0:
            return {}
        spread_pct = round((ask - bid) / ask * 100, 4)
        return {"bid": bid, "ask": ask, "spread_pct": spread_pct}
    except Exception as exc:
        print(f"{_LOG} nbbo_spread {ticker}: {exc}")
        return {}


def _td_quotes_batch(symbols: list) -> dict:
    """Batch real-time/EOD quotes.  Returns {SYM: {last, open, prevclose}}."""
    hdrs = _td_headers()
    if not hdrs or not symbols:
        return {}
    try:
        r = requests.get(
            f"{TRADIER_API_BASE}/v1/markets/quotes",
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
            f"{TRADIER_API_BASE}/v1/markets/timesales",
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
    Build the capture universe for intraday pattern research.

    Primary source: polygon_market_daily — the full 11,000-stock daily scan
    populated at 8:35 AM ET.  We query the MOST RECENT scan_date rather than
    today's date to avoid the date-mismatch bug where the scan stores
    scan_date=yesterday but the query looked for scan_date=today.

    Selects top 200 stocks by RVOL (≥1.5x) with price $2-$200 and volume
    ≥100K — the active universe most likely to have premarket + intraday
    pattern signal.  This is orders of magnitude larger than the old
    polygon_rvol_scan source (which only had 1-13 tickers due to a 3% gap
    filter + wrong scan_date).

    Secondary source: aiem_independent_picks (today's AI picks, always added).
    Returns list of dicts, capped at 200 tickers.
    """
    import psycopg2

    date_str = trade_date.strftime("%Y-%m-%d")
    results  = {}

    try:
        with psycopg2.connect(db_url) as conn, conn.cursor() as cur:

            # Source 1: polygon_market_daily — top movers by prior-day RVOL.
            # Uses MAX(scan_date) so it always finds the most recent data,
            # even if the 8:35 AM scan stored yesterday's date as scan_date.
            cur.execute("""
                SELECT
                    m.ticker,
                    m.gap_pct        AS gap,
                    m.rvol           AS rvol,
                    m.close_strength AS prior_cs,
                    m.close_price    AS prior_close,
                    m.volume         AS prior_vol
                FROM polygon_market_daily m
                WHERE m.scan_date = (
                    SELECT MAX(scan_date) FROM polygon_market_daily
                )
                  AND m.rvol        >= 1.5
                  AND m.close_price BETWEEN 2.0 AND 200.0
                  AND m.volume      >= 100000
                ORDER BY m.rvol DESC
                LIMIT 200
            """)
            for row in cur.fetchall():
                ticker, gap, rvol, prior_cs, prior_close, prior_vol = row
                results[ticker] = {
                    "ticker":               ticker,
                    "premarket_gap_pct":    float(gap or 0),
                    "premarket_rvol":       float(rvol or 0),
                    "prior_close_strength": float(prior_cs)    if prior_cs    is not None else None,
                    "prior_rvol":           float(rvol or 0),
                    "prior_close":          float(prior_close) if prior_close is not None else None,
                }

            print(f"{_LOG} universe source1 (polygon_market_daily): {len(results)} tickers")

            # Source 2: today's independent stock picks (always include)
            try:
                cur.execute("""
                    SELECT p.ticker,
                           m.close_strength AS prior_cs,
                           m.rvol           AS prior_rvol
                    FROM aiem_independent_picks p
                    LEFT JOIN LATERAL (
                        SELECT close_strength, rvol
                        FROM polygon_market_daily
                        WHERE ticker   = p.ticker
                        ORDER BY scan_date DESC
                        LIMIT 1
                    ) m ON true
                    WHERE p.pick_date = %s::date
                      AND p.pick_type = 'stock'
                """, (date_str,))
                added = 0
                for row in cur.fetchall():
                    ticker, prior_cs, prior_rvol = row
                    if ticker not in results:
                        results[ticker] = {
                            "ticker":               ticker,
                            "premarket_gap_pct":    None,
                            "premarket_rvol":       None,
                            "prior_close_strength": float(prior_cs)   if prior_cs   is not None else None,
                            "prior_rvol":           float(prior_rvol) if prior_rvol is not None else None,
                            "prior_close":          None,
                        }
                        added += 1
                if added:
                    print(f"{_LOG} universe source2 (aiem_independent_picks): +{added} tickers")
            except Exception:
                pass  # aiem_independent_picks may not exist yet

    except Exception as exc:
        print(f"{_LOG} universe query error: {exc}")
        return []

    universe = list(results.values())[:200]
    print(f"{_LOG} universe total: {len(universe)} tickers for {date_str}")
    return universe


# ── DB table ──────────────────────────────────────────────────────────────────

def init_firstcandle_table(db_url: str) -> None:
    """Create aiem_first_candle_data if it doesn't exist (idempotent).
    Also adds new indicator columns if upgrading from an older schema.
    """
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
            # Additive column upgrades — safe to run on existing tables
            for col_sql in [
                "ADD COLUMN IF NOT EXISTS premarket_high      NUMERIC",
                "ADD COLUMN IF NOT EXISTS premarket_low       NUMERIC",
                "ADD COLUMN IF NOT EXISTS premarket_high_pct  NUMERIC",
                "ADD COLUMN IF NOT EXISTS premarket_volume    BIGINT",
                "ADD COLUMN IF NOT EXISTS has_news            BOOLEAN",
                "ADD COLUMN IF NOT EXISTS news_count          INTEGER DEFAULT 0",
                # Tier 3: Intraday order-flow signals (CVD, VWAP, Volume Profile)
                "ADD COLUMN IF NOT EXISTS cum_delta           FLOAT",
                "ADD COLUMN IF NOT EXISTS delta_pct           FLOAT",
                "ADD COLUMN IF NOT EXISTS buy_vol             BIGINT",
                "ADD COLUMN IF NOT EXISTS sell_vol            BIGINT",
                "ADD COLUMN IF NOT EXISTS tick_count          INTEGER",
                "ADD COLUMN IF NOT EXISTS vwap                FLOAT",
                "ADD COLUMN IF NOT EXISTS vwap_vs_open_pct    FLOAT",
                "ADD COLUMN IF NOT EXISTS poc_price           FLOAT",
                "ADD COLUMN IF NOT EXISTS poc_vs_open_pct     FLOAT",
                "ADD COLUMN IF NOT EXISTS value_area_high     FLOAT",
                "ADD COLUMN IF NOT EXISTS value_area_low      FLOAT",
                "ADD COLUMN IF NOT EXISTS bid_ask_spread_pct  FLOAT",
            ]:
                cur.execute(f"ALTER TABLE aiem_first_candle_data {col_sql}")
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

    polygon_key = _polygon_key()

    def _capture_one(entry: dict) -> dict | None:
        ticker = entry["ticker"]
        # Fetch all 5 data sources in parallel inner threads:
        #   Tradier first candle, Tradier premarket H/L, Polygon news,
        #   Polygon tick-level order flow (CVD+VWAP+Volume Profile), Polygon NBBO
        with ThreadPoolExecutor(max_workers=5) as _inner:
            fut_candle  = _inner.submit(_td_first_candle,         ticker, trade_date)
            fut_pm      = _inner.submit(_td_premarket_hl,         ticker, trade_date)
            fut_news    = _inner.submit(_polygon_news_check,      ticker, trade_date)
            fut_intra   = _inner.submit(_polygon_intraday_signals, ticker, trade_date)
            fut_nbbo    = _inner.submit(_polygon_nbbo_spread,     ticker)
            candle  = fut_candle.result()
            pm_data = fut_pm.result()
            news    = fut_news.result()
            intra   = fut_intra.result()
            nbbo    = fut_nbbo.result()

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

        # Compute real premarket gap: prior day's close → today's open.
        prior_close = entry.get("prior_close")
        real_gap_pct = None
        if prior_close and prior_close > 0 and o > 0:
            real_gap_pct = round((o - prior_close) / prior_close * 100, 3)

        # Pre-market high extension: how far above prior close did PM reach?
        pm_high     = pm_data.get("premarket_high")
        pm_low      = pm_data.get("premarket_low")
        pm_high_pct = None
        if pm_high and prior_close and prior_close > 0:
            pm_high_pct = round((pm_high - prior_close) / prior_close * 100, 3)

        # Fill open-relative derived fields now that we have open price
        vwap        = intra.get("vwap")
        poc_price   = intra.get("poc_price")
        vwap_vs_open = None
        poc_vs_open  = None
        if vwap and o:
            vwap_vs_open = round((o - vwap) / vwap * 100, 3)
        if poc_price and o:
            poc_vs_open = round((o - poc_price) / poc_price * 100, 3)

        return {
            **entry,
            "premarket_gap_pct":      real_gap_pct,
            "open_price":             round(o, 4),
            "first_candle_high":      round(h, 4),
            "first_candle_low":       round(l, 4),
            "first_candle_close":     round(c, 4),
            "first_candle_volume":    v,
            "gap_held":               c > o,
            "first_candle_direction": direction,
            "first_candle_range_pct": rng_pct,
            # Tier 2: premarket context
            "premarket_high":         pm_high,
            "premarket_low":          pm_low,
            "premarket_high_pct":     pm_high_pct,
            "premarket_volume":       pm_data.get("premarket_volume"),
            "has_news":               news.get("has_news"),
            "news_count":             news.get("news_count", 0),
            # Tier 3: intraday order-flow (CVD + VWAP + Volume Profile)
            "cum_delta":              intra.get("cum_delta"),
            "delta_pct":              intra.get("delta_pct"),
            "buy_vol":                intra.get("buy_vol"),
            "sell_vol":               intra.get("sell_vol"),
            "tick_count":             intra.get("tick_count"),
            "vwap":                   vwap,
            "vwap_vs_open_pct":       vwap_vs_open,
            "poc_price":              poc_price,
            "poc_vs_open_pct":        poc_vs_open,
            "value_area_high":        intra.get("value_area_high"),
            "value_area_low":         intra.get("value_area_low"),
            "bid_ask_spread_pct":     nbbo.get("spread_pct"),
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
                         gap_held, first_candle_direction, first_candle_range_pct,
                         premarket_high, premarket_low, premarket_high_pct,
                         premarket_volume, has_news, news_count,
                         cum_delta, delta_pct, buy_vol, sell_vol, tick_count,
                         vwap, vwap_vs_open_pct, poc_price, poc_vs_open_pct,
                         value_area_high, value_area_low, bid_ask_spread_pct)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    row.get("premarket_high"),
                    row.get("premarket_low"),
                    row.get("premarket_high_pct"),
                    row.get("premarket_volume"),
                    row.get("has_news"),
                    row.get("news_count", 0),
                    # Tier 3 order-flow signals
                    row.get("cum_delta"),
                    row.get("delta_pct"),
                    row.get("buy_vol"),
                    row.get("sell_vol"),
                    row.get("tick_count"),
                    row.get("vwap"),
                    row.get("vwap_vs_open_pct"),
                    row.get("poc_price"),
                    row.get("poc_vs_open_pct"),
                    row.get("value_area_high"),
                    row.get("value_area_low"),
                    row.get("bid_ask_spread_pct"),
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
