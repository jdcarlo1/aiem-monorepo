"""
Signal Outcome Tracker
Stores bullish options signals when scanned, then calculates
T+3, T+5, T+10 trading-day price outcomes using yfinance.
Outcomes are stored in the DB once daily (not recomputed on every page load).
"""

import os
from datetime import date, timedelta
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("DATABASE_URL")
_ET_TZ = ZoneInfo("America/New_York")


def _et_today() -> date:
    """Most recent trading day in US/Eastern.
    - The server clock runs in UTC, so date.today() rolls to tomorrow after
      8 PM ET and would label signals under the wrong market day.
    - If ET is Saturday or Sunday (weekend scan / late-Friday refresh after
      midnight), roll back to the most recent Friday — options markets are
      closed on weekends so no valid signals can originate on those days."""
    from datetime import datetime
    d = datetime.now(_ET_TZ).date()
    # Saturday=5, Sunday=6 → roll back to Friday
    if d.weekday() == 5:   # Saturday
        d -= timedelta(days=1)
    elif d.weekday() == 6: # Sunday
        d -= timedelta(days=2)
    return d


def _connect():
    import psycopg2
    # Neon pooler rejects statement_timeout in startup options — set after connect.
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15000'")
    except Exception:
        pass
    return conn


def init_signal_outcomes_table():
    if not DATABASE_URL:
        print("[signal_outcomes] no DATABASE_URL — skipping init")
        return
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id              SERIAL PRIMARY KEY,
                ticker          TEXT    NOT NULL,
                signal_date     DATE    NOT NULL,
                session         TEXT    NOT NULL DEFAULT 'manual',
                price_at_signal REAL,
                call_put_ratio  REAL,
                premium_m       REAL,
                strike          REAL,
                expiry          TEXT,
                created_at      TIMESTAMP DEFAULT NOW(),
                UNIQUE(ticker, signal_date, session)
            )
        """)
        for col, typ in [
            ("t3_price",  "REAL"),
            ("t5_price",  "REAL"),
            ("t10_price", "REAL"),
            ("t3_pct",    "REAL"),
            ("t5_pct",    "REAL"),
            ("t10_pct",   "REAL"),
            ("t3_win",    "BOOLEAN"),
            ("t5_win",    "BOOLEAN"),
            ("t10_win",   "BOOLEAN"),
            ("outcomes_updated_at", "TIMESTAMP"),
        ]:
            cur.execute(f"ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS {col} {typ}")
        conn.commit()
        cur.close()
        conn.close()
        print("[signal_outcomes] table ready")
    except Exception as e:
        print(f"[signal_outcomes] init error: {e}")


def store_bull_flow_signals(results: list, session: str = "manual"):
    """Persist bull-flow rows (C/P >= 2 only) to the outcomes table."""
    if not DATABASE_URL or not results:
        return
    today = _et_today().isoformat()
    stored = 0
    try:
        conn = _connect()
        cur = conn.cursor()
        for row in results:
            if (row.get("call_put_ratio") or 0) < 2:
                continue
            cur.execute("""
                INSERT INTO signal_outcomes
                    (ticker, signal_date, session, price_at_signal,
                     call_put_ratio, premium_m, strike, expiry)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, signal_date, session) DO NOTHING
            """, (
                row.get("ticker"),
                today,
                session,
                row.get("price"),
                row.get("call_put_ratio"),
                row.get("premium_m"),
                row.get("strike"),
                row.get("expiry"),
            ))
            stored += 1
        conn.commit()
        cur.close()
        conn.close()
        print(f"[signal_outcomes] stored {stored} signals for {today} ({session})")
    except Exception as e:
        print(f"[signal_outcomes] store error: {e}")


def _add_trading_days(start: date, n: int) -> date:
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _closest_close(hist, target: date):
    """Return closing price on target date or nearest future trading day."""
    import pandas as pd
    dates_str = hist.index.strftime("%Y-%m-%d").tolist()
    for offset in range(7):
        key = (target + timedelta(days=offset)).isoformat()
        if key in dates_str:
            idx = dates_str.index(key)
            val = hist["Close"].iloc[idx]
            if hasattr(val, "item"):
                val = val.item()
            if isinstance(val, float) and val == val and val > 0:
                return round(float(val), 2)
    return None


def update_signal_outcome_prices():
    """
    Fill stored T+3/T+5/T+10 prices for any row where that date has now passed
    and the price column is still NULL. Runs once daily at 4:33 PM ET — never
    called on page load so Yahoo throttling never blocks the Outcomes tab.
    """
    if not DATABASE_URL:
        return
    try:
        today = _et_today()

        conn = _connect()
        cur = conn.cursor()
        cutoff = (today - timedelta(days=45)).isoformat()

        # Pick rows missing ANY horizon that is already due. Old query used only
        # `t3_price IS NULL`, so once T+3 filled the row was never revisited for
        # T+5 / T+10 — leaving t10_win permanently NULL across the table.
        cur.execute("""
            SELECT id, ticker, signal_date, price_at_signal,
                   t3_price, t5_price, t10_price
            FROM signal_outcomes
            WHERE signal_date >= %s
              AND call_put_ratio >= 2
              AND (
                    t3_price IS NULL
                 OR t5_price IS NULL
                 OR t10_price IS NULL
              )
            ORDER BY signal_date ASC
            LIMIT 500
        """, (cutoff,))
        rows = cur.fetchall()

        updated = 0
        for row_id, ticker, sig_date, price_at_signal, cur_t3, cur_t5, cur_t10 in rows:
            t3  = _add_trading_days(sig_date, 3)
            t5  = _add_trading_days(sig_date, 5)
            t10 = _add_trading_days(sig_date, 10)

            # Skip until at least T+3 is due
            if today < t3:
                continue
            # Skip if every due horizon is already filled
            need_t3 = cur_t3 is None
            need_t5 = cur_t5 is None and today >= t5
            need_t10 = cur_t10 is None and today >= t10
            if not (need_t3 or need_t5 or need_t10):
                continue

            try:
                import os as _so_os, requests as _so_req, pandas as _so_pd, datetime as _so_dt
                _tok = (_so_os.environ.get("TRADIER_API_TOKEN_2") or
                        _so_os.environ.get("TRADIER_API_TOKEN", ""))
                _days_back = (today - sig_date).days + 2
                _hist_end  = today + timedelta(days=1)
                _r = _so_req.get(
                    "https://api.tradier.com/v1/markets/history",
                    params={"symbol": ticker, "interval": "daily",
                            "start": sig_date.isoformat(),
                            "end": _hist_end.isoformat()},
                    headers={"Authorization": f"Bearer {_tok}",
                             "Accept": "application/json"},
                    timeout=10,
                )
                if _r.status_code != 200:
                    continue
                _raw = (_r.json().get("history") or {}).get("day") or []
                if isinstance(_raw, dict): _raw = [_raw]
                if not _raw: continue
                _tmp = _so_pd.DataFrame(_raw)
                _tmp["date"] = _so_pd.to_datetime(_tmp["date"])
                _tmp = _tmp.set_index("date").rename(columns={"close": "Close"})
                hist = _tmp[["Close"]]
                if hist.empty:
                    continue

                base = price_at_signal or _closest_close(hist, sig_date)
                if not base:
                    continue

                t3_p  = cur_t3 if cur_t3 is not None else _closest_close(hist, t3)
                t5_p  = cur_t5 if cur_t5 is not None else (_closest_close(hist, t5) if today >= t5 else None)
                t10_p = cur_t10 if cur_t10 is not None else (_closest_close(hist, t10) if today >= t10 else None)

                def pct(p):
                    if p is None or not base:
                        return None
                    return round((p - base) / base * 100, 2)

                cur.execute("""
                    UPDATE signal_outcomes
                    SET t3_price=%s, t5_price=%s, t10_price=%s,
                        t3_pct=%s,   t5_pct=%s,   t10_pct=%s,
                        t3_win=%s,   t5_win=%s,   t10_win=%s,
                        outcomes_updated_at=NOW()
                    WHERE id=%s
                """, (
                    t3_p, t5_p, t10_p,
                    pct(t3_p), pct(t5_p), pct(t10_p),
                    (t3_p > base) if t3_p is not None else None,
                    (t5_p > base) if t5_p is not None else None,
                    (t10_p > base) if t10_p is not None else None,
                    row_id,
                ))
                updated += 1
            except Exception as e:
                print(f"[signal_outcomes] {ticker} price lookup error: {e}")
                continue

        conn.commit()
        cur.close()
        conn.close()
        print(f"[signal_outcomes] outcomes filled for {updated} rows")
    except Exception as e:
        print(f"[signal_outcomes] update_signal_outcome_prices error: {e}")


def get_signal_outcomes(limit: int = 500) -> list:
    """
    Return stored signals with T+3, T+5, T+10 price outcomes.
    Reads from pre-computed DB columns — no live yfinance calls.
    Only returns signals where at least T+3 trading days have elapsed
    AND t3_price has been filled by the daily updater.
    Default limit raised to 500 so all weeks of history are visible, not just ~1-2 days.
    """
    if not DATABASE_URL:
        return []
    try:
        conn = _connect()
        cur = conn.cursor()
        cutoff = (_et_today() - timedelta(days=90)).isoformat()
        cur.execute("""
            SELECT ticker, signal_date, price_at_signal,
                   call_put_ratio, premium_m, strike, expiry,
                   t3_price, t5_price, t10_price,
                   t3_pct, t5_pct, t10_pct,
                   t3_win, t5_win, t10_win
            FROM signal_outcomes
            WHERE signal_date >= %s
              AND call_put_ratio >= 2
              AND t3_price IS NOT NULL
            ORDER BY signal_date DESC
            LIMIT %s
        """, (cutoff, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        outcomes = []
        for (ticker, sig_date, price_at_signal, cpr, premium_m, strike, expiry,
             t3_p, t5_p, t10_p, t3_pct, t5_pct, t10_pct,
             t3_win, t5_win, t10_win) in rows:
            outcomes.append({
                "ticker":          ticker,
                "signal_date":     sig_date.isoformat(),
                "price_at_signal": round(float(price_at_signal), 2) if price_at_signal else None,
                "call_put_ratio":  round(float(cpr), 2),
                "premium_m":       round(float(premium_m), 2) if premium_m else None,
                "strike":          strike,
                "expiry":          expiry,
                "t3_price":  t3_p,
                "t5_price":  t5_p,
                "t10_price": t10_p,
                "t3_pct":    t3_pct,
                "t5_pct":    t5_pct,
                "t10_pct":   t10_pct,
                "t3_win":    t3_win,
                "t5_win":    t5_win,
                "t10_win":   t10_win,
            })

        return outcomes

    except Exception as e:
        print(f"[signal_outcomes] get_outcomes error: {e}")
        return []
