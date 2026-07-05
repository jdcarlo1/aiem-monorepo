"""
sector_etf_data.py

Stores and maintains daily close prices for SPY + 11 GICS sector ETFs.

Used by:
  - alpha_feature_engineering.py  (sector relative strength feature)
  - alpha_train_pipeline.py       (SPY return for alpha label computation)
  - AIEM alpha scoring at pick time

Table: sector_etf_daily
  etf_ticker  TEXT        SPY, XLK, XLF, ...
  price_date  DATE
  close_price NUMERIC
  return_pct  NUMERIC     daily % return vs prior session
"""

import os
import time
import psycopg2
import numpy as np
from datetime import date, timedelta

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

_DB_URL = os.environ.get("DATABASE_URL", "")

SECTOR_ETFS = {
    "SPY":  "S&P 500 Benchmark",
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLE":  "Energy",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLB":  "Materials",
    "XLC":  "Communication Services",
}

TICKER_SECTOR_MAP = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD":  "XLK",
    "GOOGL":"XLC", "META": "XLC", "NFLX": "XLC", "CMCSA":"XLC",
    "AMZN": "XLY", "TSLA": "XLY", "NKE":  "XLY", "HD":   "XLY",
    "JPM":  "XLF", "BAC":  "XLF", "GS":   "XLF", "V":    "XLF",
    "JNJ":  "XLV", "UNH":  "XLV", "PFE":  "XLV", "MRK":  "XLV",
    "CAT":  "XLI", "DE":   "XLI", "BA":   "XLI", "UPS":  "XLI",
    "XOM":  "XLE", "CVX":  "XLE", "COP":  "XLE", "SLB":  "XLE",
    "NEE":  "XLU", "SO":   "XLU", "DUK":  "XLU",
    "PLD":  "XLRE","AMT":  "XLRE","SPG":  "XLRE",
    "LIN":  "XLB", "APD":  "XLB", "NEM":  "XLB",
    "PG":   "XLP", "KO":   "XLP", "PEP":  "XLP", "WMT":  "XLP",
}

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS sector_etf_daily (
    id          SERIAL PRIMARY KEY,
    etf_ticker  TEXT        NOT NULL,
    price_date  DATE        NOT NULL,
    close_price NUMERIC(12,4),
    return_pct  NUMERIC(8,4),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(etf_ticker, price_date)
);
CREATE INDEX IF NOT EXISTS idx_sector_etf_ticker_date
    ON sector_etf_daily(etf_ticker, price_date DESC);
"""


def _init_table():
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
    except Exception as e:
        print(f"[sector_etf] init error: {e}")


_init_table()


def _upsert_etf_rows(rows: list):
    if not rows or not _DB_URL:
        return 0
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO sector_etf_daily (etf_ticker, price_date, close_price, return_pct)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (etf_ticker, price_date) DO UPDATE
                    SET close_price = EXCLUDED.close_price,
                        return_pct  = EXCLUDED.return_pct
            """, rows)
            conn.commit()
        return len(rows)
    except Exception as e:
        print(f"[sector_etf] upsert error: {e}")
        return 0


def backfill_sector_etfs(years_back: int = 3) -> dict:
    """
    Fetch and store `years_back` years of daily closes for all ETFs.
    Returns summary dict.
    """
    if not HAS_YF:
        return {"error": "yfinance not installed"}

    start_date = date.today() - timedelta(days=years_back * 365)
    summary = {}

    for ticker in SECTOR_ETFS:
        try:
            hist = yf.download(
                ticker,
                start=start_date.isoformat(),
                end=date.today().isoformat(),
                progress=False,
                auto_adjust=True,
            )
            if hasattr(hist.columns, "levels"):
                hist.columns = hist.columns.droplevel(1)
            if hist.empty:
                summary[ticker] = {"rows": 0, "error": "empty"}
                continue

            closes = hist["Close"].dropna().sort_index()
            rows = []
            prev_close = None
            for idx, close_val in closes.items():
                d = idx.date() if hasattr(idx, "date") else idx
                c = float(close_val)
                ret = round((c / prev_close - 1) * 100, 4) if prev_close else None
                rows.append((ticker, d, round(c, 4), ret))
                prev_close = c

            n = _upsert_etf_rows(rows)
            summary[ticker] = {"rows": n}
            time.sleep(0.3)
        except Exception as e:
            summary[ticker] = {"rows": 0, "error": str(e)}
            print(f"[sector_etf] backfill {ticker} error: {e}")

    return summary


def update_sector_etfs_today() -> dict:
    """
    Fetch and store today's (and last 5 days') data for all ETFs.
    Idempotent — safe to run daily.
    """
    if not HAS_YF:
        return {"error": "yfinance not installed"}

    start_date = date.today() - timedelta(days=7)
    summary = {}

    for ticker in SECTOR_ETFS:
        try:
            hist = yf.download(
                ticker,
                start=start_date.isoformat(),
                end=(date.today() + timedelta(days=1)).isoformat(),
                progress=False,
                auto_adjust=True,
            )
            if hasattr(hist.columns, "levels"):
                hist.columns = hist.columns.droplevel(1)
            if hist.empty:
                summary[ticker] = 0
                continue

            closes = hist["Close"].dropna().sort_index()
            rows = []
            for idx, close_val in closes.items():
                d = idx.date() if hasattr(idx, "date") else idx
                rows.append((ticker, d, round(float(close_val), 4), None))

            n = _upsert_etf_rows(rows)
            summary[ticker] = n
            time.sleep(0.15)
        except Exception as e:
            summary[ticker] = 0
            print(f"[sector_etf] daily update {ticker} error: {e}")

    return summary


def get_spy_return(start_date: date, end_date: date) -> float | None:
    """
    Return SPY cumulative % return between start_date and end_date (inclusive).
    Uses sector_etf_daily if available, falls back to spy_daily_cache.
    Returns None if data unavailable.
    """
    if not _DB_URL:
        return None
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT price_date, close_price
                FROM sector_etf_daily
                WHERE etf_ticker = 'SPY'
                  AND price_date BETWEEN %s AND %s
                ORDER BY price_date
            """, (start_date, end_date))
            rows = cur.fetchall()

        if len(rows) >= 2:
            entry = float(rows[0][1])
            exit_ = float(rows[-1][1])
            return round((exit_ / entry - 1) * 100, 4)

        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT date, close
                FROM spy_daily_cache
                WHERE date BETWEEN %s AND %s
                ORDER BY date
            """, (start_date, end_date))
            rows2 = cur.fetchall()

        if len(rows2) >= 2:
            entry = float(rows2[0][1])
            exit_ = float(rows2[-1][1])
            return round((exit_ / entry - 1) * 100, 4)

        return None
    except Exception as e:
        print(f"[sector_etf] get_spy_return error: {e}")
        return None


def get_sector_etf_for_ticker(ticker: str) -> str:
    """Return the sector ETF ticker for a given stock, defaulting to SPY."""
    return TICKER_SECTOR_MAP.get(ticker.upper(), "SPY")


def get_sector_relative_strength(ticker: str, signal_date: date, window_days: int = 10) -> float | None:
    """
    Return ticker's cumulative return minus its sector ETF's cumulative return
    over the `window_days` prior to signal_date.
    Positive = outperforming the sector.
    """
    if not _DB_URL:
        return None
    sector_etf = get_sector_etf_for_ticker(ticker)
    start = signal_date - timedelta(days=window_days * 2)

    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT price_date, close_price
                FROM sector_etf_daily
                WHERE etf_ticker = %s
                  AND price_date BETWEEN %s AND %s
                ORDER BY price_date DESC
                LIMIT %s
            """, (sector_etf, start, signal_date, window_days + 1))
            etf_rows = cur.fetchall()

            cur.execute("""
                SELECT scan_date, close_price
                FROM polygon_market_daily
                WHERE ticker = %s
                  AND scan_date BETWEEN %s AND %s
                ORDER BY scan_date DESC
                LIMIT %s
            """, (ticker.upper(), start, signal_date, window_days + 1))
            stock_rows = cur.fetchall()

        if len(etf_rows) < 2 or len(stock_rows) < 2:
            return None

        etf_ret   = (float(etf_rows[0][1]) / float(etf_rows[-1][1]) - 1) * 100
        stock_ret = (float(stock_rows[0][1]) / float(stock_rows[-1][1]) - 1) * 100
        return round(stock_ret - etf_ret, 4)
    except Exception as e:
        print(f"[sector_etf] get_sector_rs error: {e}")
        return None


def get_status() -> dict:
    """Return row counts and date ranges for all ETFs in sector_etf_daily."""
    if not _DB_URL:
        return {}
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT etf_ticker, COUNT(*), MIN(price_date), MAX(price_date)
                FROM sector_etf_daily
                GROUP BY etf_ticker
                ORDER BY etf_ticker
            """)
            return {
                r[0]: {"rows": r[1], "from": str(r[2]), "to": str(r[3])}
                for r in cur.fetchall()
            }
    except Exception as e:
        return {"error": str(e)}
