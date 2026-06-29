"""
opening_snapshot_tracker.py
====================================================================
Every time this runs (same 5-min cadence as gamma scanner), it takes
a live price/volume snapshot for each candidate ticker and stores it.
Across multiple scans during the morning, these snapshots become the
"bars" used to classify opening behavior — no dedicated minute-bar
feed required.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, List

import psycopg2
import psycopg2.extras


def create_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS opening_snapshots (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(10) NOT NULL,
            scan_time TIMESTAMPTZ NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            volume BIGINT,
            scan_date DATE NOT NULL DEFAULT CURRENT_DATE
        );
        CREATE INDEX IF NOT EXISTS idx_opening_snapshots_ticker_date
            ON opening_snapshots(ticker, scan_date);
    """


def record_snapshot(db_url: str, ticker: str, price: float, volume: int) -> None:
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO opening_snapshots (ticker, scan_time, price, volume)
                VALUES (%s, NOW(), %s, %s)
            """, (ticker, price, volume))
        conn.commit()
    finally:
        conn.close()


def get_todays_snapshots(db_url: str, ticker: str) -> List[Dict[str, Any]]:
    """Returns all snapshots taken for this ticker today, oldest first —
    these ARE the self-built bars."""
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT scan_time, price, volume FROM opening_snapshots
                WHERE ticker = %s AND scan_date = CURRENT_DATE
                ORDER BY scan_time ASC
            """, (ticker,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        print(get_todays_snapshots(db_url, "AAPL"))
    else:
        print("Set DATABASE_URL to test.")
