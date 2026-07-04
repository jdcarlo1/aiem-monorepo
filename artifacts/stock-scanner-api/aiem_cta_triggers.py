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
"""
import os


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


def compute_cta_triggers_bulk(conn, min_days: int = 210, top_n: int = 500) -> list:
    """
    Compute CTA trigger levels for all tickers with sufficient history in polygon_market_daily.
    Returns list of result dicts, sorted by trigger_pct_away ascending (nearest trigger first).
    
    Args:
        conn: psycopg2 connection
        min_days: minimum trading days required for 200d MA
        top_n: maximum number of tickers to process
    """
    from datetime import date as _d
    today = _d.today().isoformat()
    results = []

    try:
        with conn.cursor() as cur:
            # Get tickers with enough history (need 200+ days for 200d MA)
            cur.execute("""
                SELECT ticker
                FROM polygon_market_daily
                GROUP BY ticker
                HAVING COUNT(*) >= %s
                ORDER BY COUNT(*) DESC
                LIMIT %s
            """, (min_days, top_n))
            tickers = [row[0] for row in cur.fetchall()]

            if not tickers:
                return []

            # Fetch last 210 closes per ticker in one query
            cur.execute("""
                SELECT ticker, scan_date, close_price
                FROM polygon_market_daily
                WHERE ticker = ANY(%s)
                  AND close_price > 0
                ORDER BY ticker, scan_date
            """, (tickers,))
            rows = cur.fetchall()

        # Group by ticker
        from collections import defaultdict
        ticker_closes: dict = defaultdict(list)
        for ticker, scan_date, close in rows:
            ticker_closes[ticker].append(float(close))

        for ticker, closes in ticker_closes.items():
            if len(closes) < min_days:
                continue

            spot = closes[-1]
            if spot <= 0:
                continue

            sma50  = _sma(closes, 50)
            sma100 = _sma(closes, 100)
            sma200 = _sma(closes, 200)

            if sma50 is None or sma200 is None:
                continue

            above_50  = spot > sma50  if sma50  else None
            above_100 = spot > sma100 if sma100 else None
            above_200 = spot > sma200 if sma200 else None

            # CTA score: each MA price is above = +1
            cta_score = sum([
                1 if above_50  else 0,
                1 if above_100 else 0,
                1 if above_200 else 0,
            ])

            # CTA label
            if cta_score == 3:
                cta_label = "MAX_LONG"
            elif cta_score == 2:
                cta_label = "MOSTLY_LONG"
            elif cta_score == 1:
                cta_label = "MOSTLY_SHORT"
            else:
                cta_label = "MAX_SHORT"

            # Nearest trigger: which MA is price closest to?
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

            # Golden/Death Cross: 50d vs 200d crossover detection
            # Check recent crossover using last 10 days
            cross_label = None
            days_since_cross = None
            if len(closes) >= 210:
                prev_closes = closes[:-1]
                for lag in range(1, 11):
                    if len(prev_closes) < lag + 200:
                        break
                    prev_spot   = prev_closes[-lag]
                    prev_sma50  = _sma(prev_closes[:-lag+1] if lag > 1 else prev_closes, 50)
                    prev_sma200 = _sma(prev_closes[:-lag+1] if lag > 1 else prev_closes, 200)
                    if prev_sma50 is None or prev_sma200 is None:
                        break
                    # Current 50>200 but previously 50<200 = golden cross
                    if sma50 > sma200 and prev_sma50 <= prev_sma200:
                        cross_label = "GOLDEN_CROSS"
                        days_since_cross = lag
                        break
                    # Current 50<200 but previously 50>200 = death cross
                    elif sma50 < sma200 and prev_sma50 >= prev_sma200:
                        cross_label = "DEATH_CROSS"
                        days_since_cross = lag
                        break

            results.append({
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
            })

    except Exception as _e:
        print(f"[cta_triggers] compute error: {_e}")

    # Sort by nearest trigger first
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
