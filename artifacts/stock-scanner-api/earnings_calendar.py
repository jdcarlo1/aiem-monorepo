"""
earnings_calendar.py
====================================================================
Per-ticker earnings date awareness, distinct from economic_calendar.py
(which is macro-level). Avoids entering new positions right before
earnings, when IV crush risk is highest.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, Optional

import psycopg2


def add_earnings_date(db_url: str, ticker: str, earnings_date: str,
                       timing: str = "unknown") -> None:
    """timing: 'before_market', 'after_market', or 'unknown'"""
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO earnings_calendar (ticker, earnings_date, timing)
                VALUES (%s, %s, %s)
                ON CONFLICT (ticker, earnings_date) DO NOTHING
            """, (ticker, earnings_date, timing))
        conn.commit()
    finally:
        conn.close()


def days_until_earnings(db_url: str, ticker: str) -> Optional[int]:
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT earnings_date FROM earnings_calendar
                WHERE ticker = %s AND earnings_date >= CURRENT_DATE
                ORDER BY earnings_date ASC LIMIT 1
            """, (ticker,))
            row = cur.fetchone()
            if not row:
                return None
            delta = (row[0] - dt.date.today()).days
            return delta
    finally:
        conn.close()


def should_avoid_entry(db_url: str, ticker: str, buffer_days: int = 2) -> Dict[str, Any]:
    """
    Returns whether a new position should be avoided due to upcoming
    earnings within buffer_days.
    """
    days_out = days_until_earnings(db_url, ticker)
    if days_out is None:
        return {"ticker": ticker, "avoid": False, "reason": "no known upcoming earnings date"}

    avoid = days_out <= buffer_days
    return {
        "ticker": ticker,
        "days_until_earnings": days_out,
        "avoid": avoid,
        "reason": f"earnings in {days_out} days" if avoid else f"earnings in {days_out} days, outside {buffer_days}-day buffer",
    }


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        print(should_avoid_entry(db_url, "AAPL"))
    else:
        print("Set DATABASE_URL to test against real data.")
