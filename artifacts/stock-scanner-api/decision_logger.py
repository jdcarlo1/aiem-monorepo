"""
decision_logger.py
---------------------
This is what actually makes a "track record" mean something. A win-rate
number on its own tells you almost nothing about whether the agent is doing
something real or just got lucky. What tells you something is being able to
read back WHY it made every call — including the calls where it decided
NOT to trade — and judging whether the reasoning holds up.

Logs every decision point with:
  - The full input state available to the agent at that moment
  - What it decided (trade / no-trade / hold / exit)
  - Its stated reasoning/confidence
  - What actually happened afterward (filled in later once known)

This is intentionally a pure logging module — it has no opinion on whether
a decision was good, and no ability to block or alter any decision. Pair it
with kill_switch.py (blocks bad behavior) and simulation_lock.py (blocks
real money) — this module's only job is making the history reviewable.

REQUIRES: AIEM_DATABASE_URL.
"""

import os
import json
import datetime as dt
from typing import Optional, Dict, Any, List

import psycopg2
import psycopg2.extras


DDL = """
CREATE TABLE IF NOT EXISTS agent_decisions (
    id SERIAL PRIMARY KEY,
    decision_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_name TEXT NOT NULL,
    ticker TEXT,
    decision_type TEXT NOT NULL CHECK (decision_type IN ('trade', 'no_trade', 'hold', 'exit')),
    direction TEXT,
    confidence NUMERIC,
    reasoning TEXT NOT NULL,
    input_state_snapshot JSONB,
    outcome_known BOOLEAN NOT NULL DEFAULT FALSE,
    outcome_return NUMERIC,
    outcome_recorded_at TIMESTAMPTZ,
    outcome_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_decisions_signal ON agent_decisions(signal_name);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_time ON agent_decisions(decision_time);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Neither AIEM_DATABASE_URL nor DATABASE_URL is set.")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[decision_logger] schema ready")


def log_decision(
    signal_name: str,
    decision_type: str,
    reasoning: str,
    ticker: Optional[str] = None,
    direction: Optional[str] = None,
    confidence: Optional[float] = None,
    input_state_snapshot: Optional[Dict[str, Any]] = None,
) -> int:
    """Log ANY decision point — including 'no_trade' and 'hold'. The
    no-trade decisions matter just as much as the trades: an agent that
    only logs when it trades gives you a survivorship-biased view of its
    own reasoning quality.
    """
    if not reasoning or not reasoning.strip():
        raise ValueError(
            "reasoning is required and cannot be empty. A decision without "
            "stated reasoning isn't reviewable, which defeats the entire "
            "purpose of this log."
        )
    if decision_type not in ("trade", "no_trade", "hold", "exit"):
        raise ValueError(f"Invalid decision_type: {decision_type}")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_decisions
                    (signal_name, ticker, decision_type, direction, confidence,
                     reasoning, input_state_snapshot)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    signal_name, ticker, decision_type, direction, confidence,
                    reasoning, json.dumps(input_state_snapshot or {}),
                ),
            )
            decision_id = cur.fetchone()[0]
        conn.commit()
    return decision_id


def record_outcome(decision_id: int, outcome_return: float, notes: str = ""):
    """Fill in what actually happened after a 'trade' decision, once known."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_decisions
                SET outcome_known = TRUE, outcome_return = %s,
                    outcome_recorded_at = now(), outcome_notes = %s
                WHERE id = %s
                """,
                (outcome_return, notes, decision_id),
            )
        conn.commit()


def get_decisions(
    signal_name: Optional[str] = None,
    decision_type: Optional[str] = None,
    since: Optional[dt.datetime] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM agent_decisions WHERE 1=1"
    params: List[Any] = []
    if signal_name:
        query += " AND signal_name = %s"
        params.append(signal_name)
    if decision_type:
        query += " AND decision_type = %s"
        params.append(decision_type)
    if since:
        query += " AND decision_time >= %s"
        params.append(since)
    query += " ORDER BY decision_time DESC LIMIT %s"
    params.append(limit)

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]

    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return rows


def decision_quality_summary(signal_name: str) -> Dict[str, Any]:
    """Basic aggregate stats — NOT a substitute for actually reading the
    reasoning text on a sample of decisions yourself."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT decision_type, count(*) as n,
                       avg(confidence) as avg_confidence,
                       avg(outcome_return) as avg_outcome_return
                FROM agent_decisions
                WHERE signal_name = %s
                GROUP BY decision_type
                """,
                (signal_name,),
            )
            by_type = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT count(*) as n
                FROM agent_decisions
                WHERE signal_name = %s AND decision_type = 'trade'
                  AND outcome_known = TRUE AND outcome_return > 0
                """,
                (signal_name,),
            )
            wins = cur.fetchone()["n"]

            cur.execute(
                """
                SELECT count(*) as n
                FROM agent_decisions
                WHERE signal_name = %s AND decision_type = 'trade'
                  AND outcome_known = TRUE
                """,
                (signal_name,),
            )
            total_known = cur.fetchone()["n"]

    return {
        "signal_name": signal_name,
        "breakdown_by_decision_type": by_type,
        "win_rate_among_known_outcomes": round(wins / total_known, 4) if total_known else None,
        "n_with_known_outcome": total_known,
        "reminder": (
            "Pull and actually READ a random sample of 'reasoning' text from "
            "get_decisions() periodically — especially from losing trades and "
            "from no_trade decisions during periods the signal would have won. "
            "That's where you'll see if it's reasoning well or just sounding "
            "confident."
        ),
    }


if __name__ == "__main__":
    init_schema()
    print("decision_logger schema ready.")
