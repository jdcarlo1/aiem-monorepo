"""
position_reconciler.py
====================================================================
Compares the broker's actual position state against what your DB
thinks is open, and flags/halts on any mismatch.

DESIGN NOTE: This module is broker-agnostic by design. It accepts a
`position_source_fn` callable that returns a list of position dicts.
Today, wire it to `mock_position_source()` below. When you pick a
broker later (Alpaca, Tradier, IBKR), write a real
`get_broker_positions()` function with the same return shape and
swap it in — nothing else in this file changes.

Expected shape from position_source_fn():
    [
        {"ticker": "AAPL", "qty": 10, "side": "long"},
        {"ticker": "TSLA", "qty": -5, "side": "short"},
        ...
    ]
====================================================================
STATUS (as of 2026-08-04): PAPER-MODE RECONCILER ENABLED.

`aiem_wiring_infra.run_paper_reconciliation()` is scheduled Mon-Fri 16:10 ET
and uses `paper_position_source()` / OPEN `aiem_paper_trades` — NEVER
`mock_position_source()`. Mock remains for isolated unit tests only.

Broker live positions still require a real brokerage API; until then paper
self-consistency is the production path. Do not schedule mock_position_source.
====================================================================
"""

import datetime as dt
from typing import Callable, Dict, Any, List

import psycopg2
import psycopg2.extras


# ──────────────────────────────────────────────────────────────────
# MOCK SOURCE — use this until a real broker is wired in.
# Replace calls to this function with a real broker API call later;
# keep the same return shape and nothing downstream needs to change.
# ──────────────────────────────────────────────────────────────────
def mock_position_source() -> List[Dict[str, Any]]:
    """
    Returns a hardcoded fake position list for testing reconciliation
    logic without a live broker connection. Edit this list manually
    to simulate mismatches and confirm the reconciler catches them.
    """
    return [
        {"ticker": "AAPL", "qty": 10, "side": "long"},
    ]


# ──────────────────────────────────────────────────────────────────
# CORE RECONCILIATION LOGIC
# ──────────────────────────────────────────────────────────────────
def get_db_open_positions(db_url: str) -> List[Dict[str, Any]]:
    """Pulls everything your system THINKS is currently open."""
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, ticker, status
                FROM ai_stock_picks
                WHERE status IS NULL OR status = 'open'
            """)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def reconcile_positions(
    db_url: str,
    position_source_fn: Callable[[], List[Dict[str, Any]]] = mock_position_source,
) -> Dict[str, Any]:
    """
    Compares broker-reported positions against DB-reported open positions.
    Returns a result dict with any mismatches found. Does NOT silently
    fix anything — mismatches require human review, by design.
    """
    broker_positions = position_source_fn()
    db_positions = get_db_open_positions(db_url)

    broker_tickers = {p["ticker"] for p in broker_positions}
    db_tickers = {p["ticker"] for p in db_positions}

    only_in_broker = broker_tickers - db_tickers   # broker has it, DB doesn't know
    only_in_db = db_tickers - broker_tickers       # DB thinks open, broker doesn't have it
    in_both = broker_tickers & db_tickers

    qty_mismatches = []
    for ticker in in_both:
        broker_qty = next(p["qty"] for p in broker_positions if p["ticker"] == ticker)
        # DB doesn't currently track live qty on ai_stock_picks in the sample
        # schema shown — add a qty column there if you want quantity-level
        # reconciliation, not just open/closed reconciliation.

    result = {
        "checked_at": dt.datetime.utcnow().isoformat(),
        "broker_position_count": len(broker_positions),
        "db_open_position_count": len(db_positions),
        "only_in_broker": sorted(only_in_broker),
        "only_in_db": sorted(only_in_db),
        "mismatch_found": bool(only_in_broker or only_in_db),
    }

    if result["mismatch_found"]:
        _log_mismatch(db_url, result)

    return result


def _log_mismatch(db_url: str, result: Dict[str, Any]) -> None:
    """
    Writes a mismatch event to a dedicated table so you have an audit
    trail. Create this table once:

        CREATE TABLE IF NOT EXISTS reconciliation_log (
            id SERIAL PRIMARY KEY,
            checked_at TIMESTAMPTZ NOT NULL,
            only_in_broker TEXT,
            only_in_db TEXT,
            mismatch_found BOOLEAN,
            resolved BOOLEAN DEFAULT FALSE
        );
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO reconciliation_log
                    (checked_at, only_in_broker, only_in_db, mismatch_found)
                VALUES (%s, %s, %s, %s)
            """, (
                result["checked_at"],
                ",".join(result["only_in_broker"]),
                ",".join(result["only_in_db"]),
                result["mismatch_found"],
            ))
        conn.commit()
    finally:
        conn.close()


def has_unresolved_mismatch(db_url: str) -> bool:
    """
    Call this BEFORE placing any new order. If True, trading should be
    blocked until a human resolves the existing mismatch.
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM reconciliation_log
                WHERE mismatch_found = TRUE AND resolved = FALSE
            """)
            count = cur.fetchone()[0]
            return count > 0
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Set DATABASE_URL to test this module.")
    else:
        result = reconcile_positions(db_url)
        print(result)
