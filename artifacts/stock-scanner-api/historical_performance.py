"""
Historical performance tracking for Smart Money scores.

Stores a score_history row every time a scan runs, then answers:
"Last time this ticker had a score like X, how did it do 1–5 days / 1–4 weeks later?"

Uses a single yfinance history call per ticker (up to 1 year back) so the
lookup is fast and does no redundant fetching.
"""
import os
from datetime import datetime, timedelta, date
from typing import Optional

import psycopg2
import yfinance as yf

_DB_URL = os.getenv("DATABASE_URL", "")


def _conn():
    return psycopg2.connect(_DB_URL)


def init_score_history_table():
    """Create the score_history table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS score_history (
        id         SERIAL PRIMARY KEY,
        ticker     TEXT        NOT NULL,
        score      INTEGER     NOT NULL,
        price      NUMERIC(12,4),
        scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_sh_ticker_ts
        ON score_history (ticker, scanned_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[hist_perf] init error: {e}")


def save_scan_scores(signals: list[dict]):
    """Persist leaderboard scores after each scan. Called from scheduler jobs."""
    if not signals:
        return
    rows = [
        (s.get("ticker", ""), s.get("smart_money_score", 0), s.get("price"))
        for s in signals
        if s.get("ticker")
    ]
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO score_history (ticker, score, price) VALUES (%s, %s, %s)",
                rows,
            )
            conn.commit()
        print(f"[hist_perf] Saved {len(rows)} score rows")
    except Exception as e:
        print(f"[hist_perf] save error: {e}")


def _get_past_instances(ticker: str, score: int,
                        score_range: int = 15,
                        max_instances: int = 20) -> list[date]:
    """Return dates (UTC) when this ticker had a similar score, oldest-first."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT scanned_at::date AS day
                FROM score_history
                WHERE ticker = %s
                  AND score  BETWEEN %s AND %s
                  AND scanned_at < NOW() - INTERVAL '1 day'
                ORDER BY day DESC
                LIMIT %s
                """,
                (ticker, score - score_range, score + score_range, max_instances),
            )
            rows = cur.fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"[hist_perf] query error: {e}")
        return []


# Trading-day offsets we want to measure
_INTERVALS = [
    ("1d",  1),
    ("2d",  2),
    ("3d",  3),
    ("4d",  4),
    ("5d",  5),
    ("1w",  7),
    ("2w", 14),
    ("3w", 21),
    ("4w", 28),
]


def _nearest_price(price_series, target_date: date) -> Optional[float]:
    """Find the closing price on or after target_date within 5 calendar days."""
    for offset in range(6):
        d = target_date + timedelta(days=offset)
        ts = str(d)
        if ts in price_series.index.astype(str).tolist():
            val = price_series[price_series.index.astype(str) == ts].iloc[0]
            return float(val) if val and val == val else None
    return None


def get_historical_performance(ticker: str, score: int) -> dict:
    """
    Returns average % return at each interval, plus instance count.
    e.g. {"count": 7, "1d": 1.2, "2d": 0.8, ..., "4w": 4.5}
    Returns {"count": 0} when no history exists yet.
    """
    instances = _get_past_instances(ticker, score)
    if not instances:
        return {"count": 0}

    # Fetch up to 1 year of daily history in one call
    try:
        hist = yf.download(ticker, period="365d", interval="1d",
                           auto_adjust=False, progress=False)
        if hist.empty:
            return {"count": 0}
        closes = hist["Close"].squeeze()
    except Exception as e:
        print(f"[hist_perf] yf download error for {ticker}: {e}")
        return {"count": 0}

    # Build index as string dates for lookup
    close_dates = closes.index.astype(str).tolist()

    sums   = {label: 0.0 for label, _ in _INTERVALS}
    counts = {label: 0   for label, _ in _INTERVALS}

    for entry_date in instances:
        # Find entry price (the day of the signal)
        entry_str = str(entry_date)
        matches = [d for d in close_dates if d >= entry_str]
        if not matches:
            continue
        entry_price_row = closes[closes.index.astype(str) == matches[0]]
        if entry_price_row.empty:
            continue
        entry_price = float(entry_price_row.iloc[0])
        if not entry_price:
            continue

        for label, cal_days in _INTERVALS:
            target = entry_date + timedelta(days=cal_days)
            target_str = str(target)
            # Find first available trading day >= target
            future_dates = [d for d in close_dates if d >= target_str]
            if not future_dates:
                continue
            future_row = closes[closes.index.astype(str) == future_dates[0]]
            if future_row.empty:
                continue
            future_price = float(future_row.iloc[0])
            if not future_price:
                continue
            pct = (future_price - entry_price) / entry_price * 100
            sums[label]   += pct
            counts[label] += 1

    result = {"count": len(instances)}
    for label, _ in _INTERVALS:
        if counts[label] > 0:
            result[label] = round(sums[label] / counts[label], 1)
        else:
            result[label] = None

    return result
