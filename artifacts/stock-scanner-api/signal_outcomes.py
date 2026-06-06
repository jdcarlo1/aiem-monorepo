"""
Signal Outcome Tracker
Stores bullish options signals when scanned, then calculates
T+3, T+5, T+10 trading-day price outcomes using yfinance.
"""

import os
from datetime import date, timedelta

DATABASE_URL = os.getenv("DATABASE_URL")


def _connect():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


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
    today = date.today().isoformat()
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


def get_signal_outcomes(limit: int = 60) -> list:
    """
    Return stored signals with T+3, T+5, T+10 price outcomes.
    Only returns signals where at least T+3 trading days have elapsed.
    """
    if not DATABASE_URL:
        return []
    try:
        import yfinance as yf

        conn = _connect()
        cur = conn.cursor()
        cutoff = (date.today() - timedelta(days=45)).isoformat()
        cur.execute("""
            SELECT DISTINCT ON (ticker, signal_date)
                ticker, signal_date, price_at_signal,
                call_put_ratio, premium_m, strike, expiry
            FROM signal_outcomes
            WHERE signal_date >= %s AND call_put_ratio >= 2
            ORDER BY ticker, signal_date, call_put_ratio DESC
            LIMIT %s
        """, (cutoff, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        today = date.today()
        outcomes = []

        for ticker, sig_date, price_at_signal, cpr, premium_m, strike, expiry in rows:
            t3 = _add_trading_days(sig_date, 3)
            t5 = _add_trading_days(sig_date, 5)
            t10 = _add_trading_days(sig_date, 10)

            if today < t3:
                continue

            try:
                hist_end = (today + timedelta(days=1)).isoformat()
                hist = yf.download(
                    ticker,
                    start=sig_date.isoformat(),
                    end=hist_end,
                    progress=False,
                    auto_adjust=True,
                )
                if hist.empty:
                    continue

                base = price_at_signal or _closest_close(hist, sig_date)
                if not base:
                    continue

                t3_p  = _closest_close(hist, t3)
                t5_p  = _closest_close(hist, t5)  if today >= t5  else None
                t10_p = _closest_close(hist, t10) if today >= t10 else None

                def pct(p):
                    if p is None:
                        return None
                    return round((p - base) / base * 100, 2)

                outcomes.append({
                    "ticker":          ticker,
                    "signal_date":     sig_date.isoformat(),
                    "price_at_signal": round(base, 2),
                    "call_put_ratio":  round(cpr, 2),
                    "premium_m":       round(premium_m, 2) if premium_m else None,
                    "strike":          strike,
                    "expiry":          expiry,
                    "t3_price":  t3_p,
                    "t5_price":  t5_p,
                    "t10_price": t10_p,
                    "t3_pct":   pct(t3_p),
                    "t5_pct":   pct(t5_p),
                    "t10_pct":  pct(t10_p),
                    "t3_win":   (t3_p  > base) if t3_p  is not None else None,
                    "t5_win":   (t5_p  > base) if t5_p  is not None else None,
                    "t10_win":  (t10_p > base) if t10_p is not None else None,
                })
            except Exception as e:
                print(f"[signal_outcomes] {ticker} price lookup error: {e}")
                continue

        outcomes.sort(key=lambda x: x["signal_date"], reverse=True)
        return outcomes

    except Exception as e:
        print(f"[signal_outcomes] get_outcomes error: {e}")
        return []
