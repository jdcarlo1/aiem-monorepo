"""
aiem_cta_triggers.py — CTA (Commodity Trading Advisor) Trigger Level Estimation

Systematic trend-following funds flip long/short when price crosses key moving averages.
This module estimates CTA positioning and trigger prices from polygon_market_daily data.

Key signals:
  • price vs 50d MA  → fast CTA signal
  • price vs 100d MA → medium CTA signal
  • price vs 200d MA → slow CTA signal (most assets managed by 200d-MA rules)
  • 50d vs 200d MA   → Golden Cross / Death Cross (major flow event)

CTA Score: 0-3 (count of MAs price is above)
  3 = MAX LONG  — all CTAs long, buying pressure exhausted
  0 = MAX SHORT — all CTAs short, selling pressure exhausted
  
Trigger Level: the nearest MA that price would need to cross to flip CTA positioning.

Data source priority:
  1. polygon_market_daily (primary — 8,626 tickers when backfill complete)
  2. Tradier daily history (fallback during backfill when polygon has < min_days rows)
"""

from aiem_broker.tradier_config import TRADIER_API_BASE

import os


# ── Curated key tickers for Tradier fallback ───────────────────────────────
# Used when polygon_market_daily backfill is still running (32/252 days).
# Covers major ETFs, mega-caps, and common institutional momentum names.
_TRADIER_FALLBACK_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "HYG",
    "NVDA", "AAPL", "MSFT", "META", "GOOGL", "AMZN", "TSLA",
    "AMD", "INTC", "AMAT", "LRCX", "MU", "SMCI",
    "JPM", "BAC", "GS", "C",
    "XOM", "CVX",
    "SOFI", "PLTR", "RIVN", "RKLB", "UPST", "COIN",
]


def _fetch_tradier_closes(ticker: str, lookback_days: int = 365) -> list:
    """
    Fetch daily close prices from Tradier for CTA MA computation.
    Returns list of floats (oldest→newest) or [] on failure.
    Uses TOKEN_2 (live brokerage) → TOKEN fallback, matching main.py convention.
    """
    try:
        import urllib.request as _ur, json as _json, datetime as _dt
        token = (os.environ.get("TRADIER_API_TOKEN_2", "")
                 or os.environ.get("TRADIER_API_TOKEN", ""))
        if not token:
            return []
        start = (_dt.date.today() - _dt.timedelta(days=lookback_days)).isoformat()
        url = (f"{TRADIER_API_BASE}/v1/markets/history"
               f"?symbol={ticker}&interval=daily&start={start}&session_filter=open")
        req = _ur.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        with _ur.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        history = (data.get("history") or {}).get("day") or []
        if isinstance(history, dict):
            history = [history]
        return [float(d["close"]) for d in history if d.get("close")]
    except Exception as _e:
        print(f"[cta_triggers] Tradier fallback failed for {ticker}: {_e}")
        return []


def init_db(conn):
    """Create cta_trigger_scan table."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cta_trigger_scan (
                ticker          TEXT NOT NULL,
                scan_date       DATE NOT NULL,
                close_price     NUMERIC,
                sma_50          NUMERIC,
                sma_100         NUMERIC,
                sma_200         NUMERIC,
                above_50        BOOLEAN,
                above_100       BOOLEAN,
                above_200       BOOLEAN,
                cta_score       INT,
                cta_label       TEXT,
                trigger_price   NUMERIC,
                trigger_ma      TEXT,
                trigger_pct_away NUMERIC,
                cross_50_200    TEXT,
                days_since_cross INT,
                updated_at      TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (ticker, scan_date)
            )
        """)
        conn.commit()


def _sma(closes: list, period: int) -> float | None:
    """Simple moving average of last N closes."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _compute_cta_for_ticker(ticker: str, closes: list, today: str) -> dict | None:
    """
    Core CTA computation for a single ticker given its closes list.
    Returns result dict or None if insufficient data.
    """
    if len(closes) < 200:
        return None

    spot = closes[-1]
    if spot <= 0:
        return None

    sma50  = _sma(closes, 50)
    sma100 = _sma(closes, 100)
    sma200 = _sma(closes, 200)

    if sma50 is None or sma200 is None:
        return None

    above_50  = spot > sma50  if sma50  else None
    above_100 = spot > sma100 if sma100 else None
    above_200 = spot > sma200 if sma200 else None

    cta_score = sum([
        1 if above_50  else 0,
        1 if above_100 else 0,
        1 if above_200 else 0,
    ])

    if cta_score == 3:
        cta_label = "MAX_LONG"
    elif cta_score == 2:
        cta_label = "MOSTLY_LONG"
    elif cta_score == 1:
        cta_label = "MOSTLY_SHORT"
    else:
        cta_label = "MAX_SHORT"

    ma_levels = [
        ("SMA50",  sma50),
        ("SMA100", sma100),
        ("SMA200", sma200),
    ]
    ma_levels = [(name, val) for name, val in ma_levels if val is not None]
    if ma_levels:
        nearest = min(ma_levels, key=lambda x: abs(spot - x[1]))
        trigger_ma    = nearest[0]
        trigger_price = round(nearest[1], 2)
        trigger_pct   = round(abs(spot - nearest[1]) / spot * 100, 2)
    else:
        trigger_ma = trigger_price = trigger_pct = None

    cross_label = None
    days_since_cross = None
    if len(closes) >= 210:
        prev_closes = closes[:-1]
        for lag in range(1, 11):
            if len(prev_closes) < lag + 200:
                break
            prev_sma50  = _sma(prev_closes[:-lag+1] if lag > 1 else prev_closes, 50)
            prev_sma200 = _sma(prev_closes[:-lag+1] if lag > 1 else prev_closes, 200)
            if prev_sma50 is None or prev_sma200 is None:
                break
            if sma50 > sma200 and prev_sma50 <= prev_sma200:
                cross_label = "GOLDEN_CROSS"
                days_since_cross = lag
                break
            elif sma50 < sma200 and prev_sma50 >= prev_sma200:
                cross_label = "DEATH_CROSS"
                days_since_cross = lag
                break

    return {
        "ticker":           ticker,
        "scan_date":        today,
        "close_price":      round(spot, 2),
        "sma_50":           round(sma50, 2)  if sma50  else None,
        "sma_100":          round(sma100, 2) if sma100 else None,
        "sma_200":          round(sma200, 2) if sma200 else None,
        "above_50":         above_50,
        "above_100":        above_100,
        "above_200":        above_200,
        "cta_score":        cta_score,
        "cta_label":        cta_label,
        "trigger_price":    trigger_price,
        "trigger_ma":       trigger_ma,
        "trigger_pct_away": trigger_pct,
        "cross_50_200":     cross_label,
        "days_since_cross": days_since_cross,
    }


def compute_cta_triggers_bulk(conn, min_days: int = 210, top_n: int = 500) -> list:
    """
    Compute CTA trigger levels for tickers with sufficient price history.

    Data source priority:
    1. polygon_market_daily: use all tickers with >= min_days rows (full universe
       once backfill is complete — 8,626 tickers with 252 days each).
    2. Tradier fallback: when polygon has < min_days rows for any ticker (e.g.
       during initial backfill when only 32/252 days are available), fetch 280
       days from Tradier for _TRADIER_FALLBACK_TICKERS (curated 32-ticker list
       covering major ETFs and institutional momentum names).

    Returns list of result dicts sorted by trigger_pct_away ascending.
    """
    from datetime import date as _d
    today = _d.today().isoformat()
    results = []

    # ── Primary: polygon_market_daily ──────────────────────────────────────
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker
                FROM polygon_market_daily
                GROUP BY ticker
                HAVING COUNT(*) >= %s
                ORDER BY COUNT(*) DESC
                LIMIT %s
            """, (min_days, top_n))
            tickers = [row[0] for row in cur.fetchall()]

        if tickers:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ticker, scan_date, close_price
                    FROM polygon_market_daily
                    WHERE ticker = ANY(%s)
                      AND close_price > 0
                    ORDER BY ticker, scan_date
                """, (tickers,))
                rows = cur.fetchall()

            from collections import defaultdict
            ticker_closes: dict = defaultdict(list)
            for ticker, scan_date, close in rows:
                ticker_closes[ticker].append(float(close))

            for ticker, closes in ticker_closes.items():
                r = _compute_cta_for_ticker(ticker, closes, today)
                if r:
                    results.append(r)

    except Exception as _e:
        print(f"[cta_triggers] polygon compute error: {_e}")

    # ── Fallback: Tradier for curated tickers when polygon data is sparse ──
    if not results:
        print("[cta_triggers] polygon_market_daily insufficient — using Tradier fallback")
        for ticker in _TRADIER_FALLBACK_TICKERS:
            closes = _fetch_tradier_closes(ticker, lookback_days=365)
            if len(closes) < 200:
                print(f"[cta_triggers] {ticker}: only {len(closes)} Tradier rows, skip")
                continue
            r = _compute_cta_for_ticker(ticker, closes, today)
            if r:
                results.append(r)
                print(f"[cta_triggers] {ticker}: score={r['cta_score']} {r['cta_label']} "
                      f"trigger={r['trigger_ma']} {r['trigger_pct_away']:.1f}% away")

    results.sort(key=lambda x: x.get("trigger_pct_away") or 999)
    return results


def save_to_db(results: list, conn) -> int:
    """Upsert CTA trigger scan results."""
    n = 0
    with conn.cursor() as cur:
        for r in results:
            try:
                cur.execute("""
                    INSERT INTO cta_trigger_scan
                        (ticker, scan_date, close_price,
                         sma_50, sma_100, sma_200,
                         above_50, above_100, above_200,
                         cta_score, cta_label,
                         trigger_price, trigger_ma, trigger_pct_away,
                         cross_50_200, days_since_cross, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (ticker, scan_date) DO UPDATE SET
                        close_price=EXCLUDED.close_price,
                        sma_50=EXCLUDED.sma_50, sma_100=EXCLUDED.sma_100,
                        sma_200=EXCLUDED.sma_200,
                        above_50=EXCLUDED.above_50, above_100=EXCLUDED.above_100,
                        above_200=EXCLUDED.above_200,
                        cta_score=EXCLUDED.cta_score, cta_label=EXCLUDED.cta_label,
                        trigger_price=EXCLUDED.trigger_price,
                        trigger_ma=EXCLUDED.trigger_ma,
                        trigger_pct_away=EXCLUDED.trigger_pct_away,
                        cross_50_200=EXCLUDED.cross_50_200,
                        days_since_cross=EXCLUDED.days_since_cross,
                        updated_at=NOW()
                """, (
                    r["ticker"], r["scan_date"], r.get("close_price"),
                    r.get("sma_50"), r.get("sma_100"), r.get("sma_200"),
                    r.get("above_50"), r.get("above_100"), r.get("above_200"),
                    r.get("cta_score"), r.get("cta_label"),
                    r.get("trigger_price"), r.get("trigger_ma"),
                    r.get("trigger_pct_away"),
                    r.get("cross_50_200"), r.get("days_since_cross"),
                ))
                n += 1
            except Exception as _e:
                conn.rollback()
    conn.commit()
    return n


def query_cta_triggers(conn, cta_score_filter: int | None = None,
                       cross_only: bool = False,
                       near_trigger_pct: float = 3.0,
                       limit: int = 50) -> list:
    """
    Query recent CTA trigger scan results from DB.
    
    Args:
        cta_score_filter: filter by exact CTA score (0-3), or None for all
        cross_only: only return recent golden/death cross tickers
        near_trigger_pct: only return tickers within this % of a trigger flip
        limit: max rows
    """
    try:
        with conn.cursor() as cur:
            clauses = ["scan_date = (SELECT MAX(scan_date) FROM cta_trigger_scan)"]
            params = []
            if cta_score_filter is not None:
                clauses.append("cta_score = %s")
                params.append(cta_score_filter)
            if cross_only:
                clauses.append("cross_50_200 IS NOT NULL")
            if near_trigger_pct:
                clauses.append("trigger_pct_away <= %s")
                params.append(near_trigger_pct)

            where = " AND ".join(clauses)
            cur.execute(f"""
                SELECT ticker, close_price, sma_50, sma_100, sma_200,
                       cta_score, cta_label, trigger_price, trigger_ma,
                       trigger_pct_away, cross_50_200, days_since_cross
                FROM cta_trigger_scan
                WHERE {where}
                ORDER BY trigger_pct_away ASC NULLS LAST
                LIMIT %s
            """, params + [limit])
            cols = ["ticker","close_price","sma_50","sma_100","sma_200",
                    "cta_score","cta_label","trigger_price","trigger_ma",
                    "trigger_pct_away","cross_50_200","days_since_cross"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as _e:
        return [{"error": str(_e)}]
