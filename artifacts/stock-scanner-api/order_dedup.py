"""
order_dedup.py
====================================================================
Prevents a single AIEM decision from ever resulting in more than one
order attempt — even if the scheduler job retries, double-fires, or
a network hiccup causes a duplicate call.

Works off the existing decision_id system already in decision_logger.py.
This module is broker-agnostic: call should_place_order() BEFORE your
order-placement code runs (whichever broker that ends up being), and
call mark_order_placed() immediately AFTER a successful placement.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, Optional

import psycopg2
import psycopg2.extras


def should_place_order(db_url: str, decision_id: int) -> bool:
    """
    Returns True if this decision_id has NOT yet resulted in an order.
    Returns False if an order already exists for this decision_id —
    meaning the caller should skip placing a new one.
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM order_execution_log
                WHERE decision_id = %s
            """, (decision_id,))
            existing_count = cur.fetchone()[0]
            return existing_count == 0
    finally:
        conn.close()


def mark_order_placed(
    db_url: str,
    decision_id: int,
    broker_order_id: str,
    ticker: str,
    side: str,
    qty: float,
    status: str = "submitted",
) -> None:
    """
    Records that an order attempt was made for this decision_id.
    Call this immediately after submitting to the broker, BEFORE
    waiting for fill confirmation — the goal is to close the
    duplicate-order window as early as possible.

    Create this table once:

        CREATE TABLE IF NOT EXISTS order_execution_log (
            id SERIAL PRIMARY KEY,
            decision_id INTEGER NOT NULL,
            broker_order_id TEXT,
            ticker TEXT,
            side TEXT,
            qty DOUBLE PRECISION,
            status TEXT,
            submitted_at TIMESTAMPTZ NOT NULL,
            filled_at TIMESTAMPTZ,
            fill_price DOUBLE PRECISION,
            UNIQUE (decision_id)
        );

    The UNIQUE constraint on decision_id is the real safety net here —
    even if should_place_order() is somehow bypassed or a race condition
    slips through, the database itself will reject a second insert for
    the same decision_id.
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO order_execution_log
                    (decision_id, broker_order_id, ticker, side, qty, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO NOTHING
            """, (
                decision_id,
                broker_order_id,
                ticker,
                side,
                qty,
                status,
                dt.datetime.utcnow(),
            ))
        conn.commit()
    finally:
        conn.close()


def update_fill(
    db_url: str,
    decision_id: int,
    fill_price: float,
    status: str = "filled",
) -> None:
    """Call once the broker confirms the actual fill."""
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE order_execution_log
                SET status = %s, fill_price = %s, filled_at = %s
                WHERE decision_id = %s
            """, (status, fill_price, dt.datetime.utcnow(), decision_id))
        conn.commit()
    finally:
        conn.close()


def get_order_for_decision(db_url: str, decision_id: int) -> Optional[Dict[str, Any]]:
    """Look up whatever order (if any) exists for a given decision_id."""
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM order_execution_log WHERE decision_id = %s
            """, (decision_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Set DATABASE_URL to test this module.")
    else:
        # Example dry-run: decision_id 99999 should not exist yet
        print("Should place order for test decision 99999:",
              should_place_order(db_url, 99999))
