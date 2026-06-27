"""
shadow_ledger.py
-----------------
Paper-trading shadow mode for newly discovered signals.
Records what a signal WOULD have done against live data, with zero
money, zero customer-facing alerts, zero writes to production tables.
No order-placement capability by design.
"""

import os
import json
import datetime as dt
from typing import Optional, Dict, Any, List

import psycopg2
import psycopg2.extras


DDL = """
CREATE TABLE IF NOT EXISTS shadow_positions (
    id SERIAL PRIMARY KEY,
    signal_name TEXT NOT NULL,
    hypothesis_id INT,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price NUMERIC NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    exit_price NUMERIC,
    exit_time TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    notes TEXT,
    raw_signal_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shadow_promotion_windows (
    id SERIAL PRIMARY KEY,
    signal_name TEXT NOT NULL,
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    min_trades_required INT NOT NULL DEFAULT 20,
    promoted BOOLEAN NOT NULL DEFAULT FALSE,
    promoted_at TIMESTAMPTZ,
    promotion_decision_notes TEXT
);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL found (set AIEM_DATABASE_URL or DATABASE_URL).")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[shadow_ledger] schema ready")


def start_shadow_window(signal_name: str, weeks: int = 5, min_trades_required: int = 20) -> int:
    start = dt.date.today()
    end   = start + dt.timedelta(weeks=weeks)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO shadow_promotion_windows
                    (signal_name, window_start, window_end, min_trades_required)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (signal_name, start, end, min_trades_required),
            )
            window_id = cur.fetchone()[0]
        conn.commit()
    return window_id


def open_shadow_position(
    signal_name: str,
    ticker: str,
    direction: str,
    entry_price: float,
    entry_time: Optional[dt.datetime] = None,
    hypothesis_id: Optional[int] = None,
    raw_signal_payload: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
) -> int:
    entry_time = entry_time or dt.datetime.now(dt.timezone.utc)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO shadow_positions
                    (signal_name, hypothesis_id, ticker, direction, entry_price,
                     entry_time, raw_signal_payload, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    signal_name, hypothesis_id, ticker, direction, entry_price,
                    entry_time, json.dumps(raw_signal_payload or {}), notes,
                ),
            )
            pos_id = cur.fetchone()[0]
        conn.commit()
    return pos_id


def close_shadow_position(
    position_id: int,
    exit_price: float,
    exit_time: Optional[dt.datetime] = None,
    notes: Optional[str] = None,
):
    exit_time = exit_time or dt.datetime.now(dt.timezone.utc)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE shadow_positions
                SET exit_price = %s, exit_time = %s, status = 'closed',
                    notes = COALESCE(%s, notes)
                WHERE id = %s AND status = 'open'
                """,
                (exit_price, exit_time, notes, position_id),
            )
        conn.commit()


def shadow_performance(signal_name: str) -> Dict[str, Any]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT direction, entry_price, exit_price, ticker, entry_time, exit_time
                FROM shadow_positions
                WHERE signal_name = %s AND status = 'closed'
                ORDER BY exit_time DESC
                """,
                (signal_name,),
            )
            rows = cur.fetchall()

    if not rows:
        return {"signal_name": signal_name, "trades": 0, "win_rate": None, "avg_return": None}

    wins, returns = 0, []
    for r in rows:
        entry  = float(r["entry_price"])
        exit_  = float(r["exit_price"])
        ret    = (exit_ - entry) / entry if r["direction"] == "long" else (entry - exit_) / entry
        returns.append(ret)
        if ret > 0:
            wins += 1

    return {
        "signal_name":   signal_name,
        "trades":        len(rows),
        "win_rate":      round(wins / len(rows), 4),
        "avg_return":    round(sum(returns) / len(returns), 5),
        "best_return":   round(max(returns), 5),
        "worst_return":  round(min(returns), 5),
        "total_return":  round(sum(returns), 5),
    }


def list_open_positions(signal_name: Optional[str] = None) -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if signal_name:
                cur.execute(
                    "SELECT * FROM shadow_positions WHERE signal_name=%s AND status='open' ORDER BY entry_time DESC",
                    (signal_name,),
                )
            else:
                cur.execute(
                    "SELECT * FROM shadow_positions WHERE status='open' ORDER BY entry_time DESC LIMIT 100"
                )
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                for k in ("entry_time", "exit_time", "created_at"):
                    if d.get(k):
                        d[k] = d[k].isoformat()
                rows.append(d)
            return rows


def check_promotion_eligibility(window_id: int) -> Dict[str, Any]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM shadow_promotion_windows WHERE id = %s", (window_id,)
            )
            window = cur.fetchone()
            if not window:
                raise ValueError(f"No promotion window with id={window_id}")

            cur.execute(
                "SELECT count(*) AS n FROM shadow_positions WHERE signal_name = %s AND status = 'closed'",
                (window["signal_name"],),
            )
            n_trades = cur.fetchone()["n"]

    window_complete = dt.date.today() >= window["window_end"]
    enough_trades   = n_trades >= window["min_trades_required"]
    perf            = shadow_performance(window["signal_name"])

    return {
        "signal_name":              window["signal_name"],
        "window_start":             str(window["window_start"]),
        "window_end":               str(window["window_end"]),
        "window_complete":          window_complete,
        "enough_trades":            enough_trades,
        "trades_so_far":            n_trades,
        "trades_required":          window["min_trades_required"],
        "eligible_for_human_review": window_complete and enough_trades,
        "performance":              perf,
    }


def mark_promoted(window_id: int, decision_notes: str):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE shadow_promotion_windows
                SET promoted = TRUE, promoted_at = now(), promotion_decision_notes = %s
                WHERE id = %s
                """,
                (decision_notes, window_id),
            )
        conn.commit()


if __name__ == "__main__":
    init_schema()
    print("shadow_ledger schema ready.")
