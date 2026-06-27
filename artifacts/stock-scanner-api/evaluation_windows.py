"""
evaluation_windows.py
------------------------
Prevents "I'll just let it keep going" from becoming the default. The agent
runs autonomously for a fixed, defined window (e.g. 4 weeks), then AUTOMATICALLY
stops and produces a report. It cannot resume on its own — you have to read
the report and explicitly start the next window yourself.

This is the module that actually enforces "I'd need to see months of
consistent success before doing anything with real money" as a structural
property of the system, not just a personal intention that's easy to drift
away from after a good week.

Design:
  1. A window has a hard end date set at creation time. Nothing in this
     module allows extending a window in place — you close it out and
     deliberately open a new one, which is what creates the natural
     checkpoint.
  2. The agent's run-loop should call is_window_active() before doing
     anything, and stop entirely once it returns False.
  3. Closing a window automatically pulls in benchmark_comparison and
     decision_logger summaries so the checkpoint report is self-contained.

REQUIRES: AIEM_DATABASE_URL. Pairs with decision_logger.py and
benchmark_comparison.py for the full checkpoint report.
"""

import os
import json
import datetime as dt
from typing import Optional, Dict, Any, List

import psycopg2
import psycopg2.extras

import decision_logger as dl


DDL = """
CREATE TABLE IF NOT EXISTS evaluation_windows (
    id SERIAL PRIMARY KEY,
    signal_name TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    starting_paper_equity NUMERIC NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    closed_at TIMESTAMPTZ,
    checkpoint_report JSONB,
    human_decision TEXT,
    human_decision_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_eval_windows_signal ON evaluation_windows(signal_name);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL")
    if not url:
        raise RuntimeError("AIEM_DATABASE_URL is not set.")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[evaluation_windows] schema ready")


def start_window(signal_name: str, starting_paper_equity: float, weeks: int = 4) -> int:
    """Creates a new evaluation window. Refuses if signal_name already has
    an active window — close it first, deliberately."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM evaluation_windows WHERE signal_name = %s AND status = 'active'",
                (signal_name,),
            )
            if cur.fetchone():
                raise ValueError(
                    f"Signal '{signal_name}' already has an active evaluation window. "
                    f"Close it via close_window() before starting a new one."
                )

            start = dt.datetime.now(dt.timezone.utc)
            end   = start + dt.timedelta(weeks=weeks)
            cur.execute(
                """
                INSERT INTO evaluation_windows
                    (signal_name, window_start, window_end, starting_paper_equity)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (signal_name, start, end, starting_paper_equity),
            )
            window_id = cur.fetchone()[0]
        conn.commit()
    return window_id


def is_window_active(signal_name: str) -> Dict[str, Any]:
    """Call this before the agent does ANYTHING in its run loop. If
    'active' is False, the agent must stop — no new decisions, no new
    paper trades, nothing — until you start a new window yourself."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM evaluation_windows WHERE signal_name = %s AND status = 'active'",
                (signal_name,),
            )
            window = cur.fetchone()

    if not window:
        return {"active": False, "reason": "No active window — call start_window() first."}

    now = dt.datetime.now(dt.timezone.utc)
    if now >= window["window_end"]:
        return {
            "active": False,
            "reason": "Window end time reached — call close_window() to checkpoint.",
            "window_id": window["id"],
        }

    return {
        "active": True,
        "window_id": window["id"],
        "window_end": window["window_end"].isoformat(),
    }


def close_window(
    window_id: int,
    ending_paper_equity: float,
    benchmark_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Closes the window and produces a self-contained checkpoint report.
    After this call, is_window_active() returns False for this signal until
    you explicitly call start_window() again — there is no automatic resumption.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM evaluation_windows WHERE id = %s", (window_id,))
            window = cur.fetchone()
            if not window:
                raise ValueError(f"No window with id={window_id}")
            if window["status"] == "closed":
                raise ValueError(f"Window {window_id} is already closed.")

    decision_summary = dl.decision_quality_summary(window["signal_name"])

    total_return_pct = round(
        (ending_paper_equity - float(window["starting_paper_equity"]))
        / float(window["starting_paper_equity"]) * 100,
        2,
    )

    report = {
        "signal_name":          window["signal_name"],
        "window_start":         window["window_start"].isoformat(),
        "window_end":           window["window_end"].isoformat(),
        "starting_paper_equity": float(window["starting_paper_equity"]),
        "ending_paper_equity":  ending_paper_equity,
        "total_return_pct":     total_return_pct,
        "decision_summary":     decision_summary,
        "benchmark_comparison": benchmark_report,
        "checkpoint_note": (
            "This window is now CLOSED. The agent will not run again for this "
            "signal until you read this report and explicitly call start_window() "
            "for a new period. Before doing so, ask: did this window's performance "
            "look like a real, explainable edge, or could it have been one of "
            "several plausible-looking results pure variance would produce? "
            "One good window is a data point, not a verdict."
        ),
    }

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE evaluation_windows
                SET status = 'closed', closed_at = now(), checkpoint_report = %s
                WHERE id = %s
                """,
                (json.dumps(report), window_id),
            )
        conn.commit()

    return report


def record_human_decision(window_id: int, decision_note: str):
    """After reading the checkpoint report, log what you actually decided
    to do next. This becomes part of your own track record of how you're
    evaluating the agent over time."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE evaluation_windows
                SET human_decision = %s, human_decision_at = now()
                WHERE id = %s
                """,
                (decision_note, window_id),
            )
        conn.commit()


def get_window_history(signal_name: str) -> List[Dict[str, Any]]:
    """Full history of all windows for a signal — this IS the track record
    you're trying to build."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, signal_name, window_start, window_end, starting_paper_equity, "
                "status, closed_at, total_return_pct, human_decision, human_decision_at "
                "FROM evaluation_windows WHERE signal_name = %s ORDER BY window_start ASC",
                (signal_name,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return rows


if __name__ == "__main__":
    init_schema()
    print("evaluation_windows schema ready.")
    print("Agent must call is_window_active() before every run-loop iteration.")
