"""
economic_calendar.py
====================================================================
Tracks high-impact economic events (FOMC, CPI, NFP, etc.) so AIEM can
reduce position size or pause new entries around known volatility
catalysts. Without a paid economic calendar API, this uses a manually
maintained table you update periodically — honest tradeoff for a
solo-builder budget.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, List

import psycopg2


HIGH_IMPACT_EVENT_TYPES = {"FOMC", "CPI", "NFP", "PCE", "GDP", "FED_SPEECH"}


def create_calendar_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS economic_calendar (
            id SERIAL PRIMARY KEY,
            event_date DATE NOT NULL,
            event_time TIME,
            event_type VARCHAR(30) NOT NULL,
            description TEXT,
            impact_level VARCHAR(10) DEFAULT 'high'
        );
    """


def add_event(db_url: str, event_date: str, event_type: str,
              description: str = "", event_time: str = None,
              impact_level: str = "high") -> None:
    """Manually add a known upcoming event. Update this weekly/monthly
    from a public source like the Fed's published meeting calendar."""
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO economic_calendar (event_date, event_time, event_type, description, impact_level)
                VALUES (%s, %s, %s, %s, %s)
            """, (event_date, event_time, event_type, description, impact_level))
        conn.commit()
    finally:
        conn.close()


def get_events_today(db_url: str) -> List[Dict[str, Any]]:
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT event_type, description, impact_level, event_time
                FROM economic_calendar
                WHERE event_date = CURRENT_DATE
            """)
            cols = ["event_type", "description", "impact_level", "event_time"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def is_high_impact_day(db_url: str) -> Dict[str, Any]:
    """
    Call this before placing new entries. If True, AIEM should reduce
    position size or skip new entries today — same defensive posture
    pattern as the regime detector's high-vol regimes.
    """
    events = get_events_today(db_url)
    high_impact_today = any(
        e["event_type"] in HIGH_IMPACT_EVENT_TYPES or e["impact_level"] == "high"
        for e in events
    )
    return {
        "checked_at": dt.datetime.utcnow().isoformat(),
        "events_today": events,
        "high_impact_day": high_impact_today,
    }


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        print(is_high_impact_day(db_url))
    else:
        print("Set DATABASE_URL to test against real data.")
