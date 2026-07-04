"""
kill_switch.py
-----------------
Watches paper-trading activity for runaway behavior (e.g. revenge-trading
after a loss, excessive trade frequency, blown drawdown ceiling) and halts
the agent immediately when a limit is breached.

This is what lets you safely walk away and let the agent run unattended for
a while — instead of needing to babysit it, you trust the ceilings to catch
it if it goes off the rails, and you find out why when you check back in.

Design:
  1. Limits are checked BEFORE every new decision, not just periodically —
     the agent calls check_kill_switch() and must respect a True "halted"
     response by stopping, not placing any further paper trades.
  2. Once halted, the halt PERSISTS (written to disk/DB) until you
     explicitly clear it — the agent cannot un-halt itself.
  3. Multiple independent ceilings (drawdown, trade count, loss streak)
     so one weird metric doesn't have to do all the work.

REQUIRES: AIEM_DATABASE_URL.
"""

import os
import json
import datetime as dt
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import psycopg2
import psycopg2.extras


DDL = """
CREATE TABLE IF NOT EXISTS kill_switch_state (
    id INT PRIMARY KEY DEFAULT 1,
    halted BOOLEAN NOT NULL DEFAULT FALSE,
    halted_at TIMESTAMPTZ,
    halted_reason TEXT,
    cleared_at TIMESTAMPTZ,
    cleared_by TEXT,
    CHECK (id = 1)
);
INSERT INTO kill_switch_state (id, halted) VALUES (1, FALSE) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS kill_switch_events (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    reason TEXT,
    metrics_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
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
    print("[kill_switch] schema ready")


@dataclass
class KillSwitchLimits:
    max_drawdown_pct: float = 10.0
    max_trades_per_day: int = 25
    max_consecutive_losses: int = 6
    max_total_trades: Optional[int] = None


def _is_currently_halted() -> Optional[str]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT halted, halted_reason FROM kill_switch_state WHERE id = 1")
            row = cur.fetchone()
            return row[1] if row and row[0] else None


def _halt(reason: str, metrics_snapshot: Dict[str, Any]):
    # INTENTIONAL BEHAVIOR (documented 2026-07-04):
    # _halt() writes the DB halt flag and blocks all new paper trades.
    # It does NOT cancel any open broker orders, does NOT call any broker API,
    # and does NOT flatten existing open positions.
    # Rationale: the kill switch fires on system-level metrics (loss limit,
    # consecutive losses), not on market conditions. Forcing an exit at the
    # exact moment the system flagged itself as unreliable risks locking in
    # a bad price on a decision made under compromised conditions. Existing
    # positions were opened with exit logic decided under normal conditions,
    # which is more trustworthy than a reactive flatten at halt time.
    # SEPARATE KNOWN GAP: write_paper_pick() does not set a stop_loss or
    # target_price at entry — positions opened by premarket_open_trader have
    # no bounded exit defined at open. This is a separate issue from the kill
    # switch behavior and must be addressed independently.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kill_switch_state
                SET halted = TRUE, halted_at = now(), halted_reason = %s,
                    cleared_at = NULL, cleared_by = NULL
                WHERE id = 1
                """,
                (reason,),
            )
            cur.execute(
                "INSERT INTO kill_switch_events (event_type, reason, metrics_snapshot) VALUES ('halt', %s, %s)",
                (reason, json.dumps(metrics_snapshot)),
            )
        conn.commit()


def clear_halt(cleared_by: str, note: str = ""):
    """The ONLY way to resume after a halt — must be called explicitly by
    you (or whoever you designate), never automatically by the agent."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kill_switch_state
                SET halted = FALSE, cleared_at = now(), cleared_by = %s
                WHERE id = 1
                """,
                (cleared_by,),
            )
            cur.execute(
                "INSERT INTO kill_switch_events (event_type, reason, metrics_snapshot) VALUES ('clear', %s, %s)",
                (f"Cleared by {cleared_by}: {note}", json.dumps({})),
            )
        conn.commit()


def check_kill_switch(
    signal_name: str,
    current_equity: float,
    peak_equity: float,
    trades_today: int,
    consecutive_losses: int,
    total_trades_this_window: int,
    limits: Optional[KillSwitchLimits] = None,
) -> Dict[str, Any]:
    """Call this BEFORE every new paper-trading decision. If 'halted' is
    True in the response, the agent must NOT place the trade it was about
    to place — full stop, regardless of how confident it is in the signal.

    WHAT THIS DELIVERS: "no new trades" — new paper-trade placement is blocked.
    WHAT THIS DOES NOT DELIVER: "fully de-risked" — this function does NOT
    cancel any open orders, does NOT call any broker or paper-trading API to
    close positions, and does NOT flatten existing open exposure. Positions
    already open continue to run under whatever exit logic was set at entry.

    This behavior is intentional. See _halt() for the full rationale.

    RESET: once halted, the agent cannot un-halt itself. A halt persists in
    the DB until clear_halt() is called explicitly by a human operator.
    """
    limits = limits or KillSwitchLimits()

    existing_halt_reason = _is_currently_halted()
    if existing_halt_reason:
        return {"halted": True, "reason": existing_halt_reason, "newly_halted": False}

    drawdown_pct = ((peak_equity - current_equity) / peak_equity * 100) if peak_equity > 0 else 0.0
    metrics = {
        "signal_name": signal_name,
        "current_equity": current_equity,
        "peak_equity": peak_equity,
        "drawdown_pct": round(drawdown_pct, 2),
        "trades_today": trades_today,
        "consecutive_losses": consecutive_losses,
        "total_trades_this_window": total_trades_this_window,
    }

    breach_reason = None
    if drawdown_pct >= limits.max_drawdown_pct:
        breach_reason = f"Drawdown {drawdown_pct:.1f}% breached limit of {limits.max_drawdown_pct}%"
    elif trades_today >= limits.max_trades_per_day:
        breach_reason = f"Trades today ({trades_today}) breached daily limit of {limits.max_trades_per_day}"
    elif consecutive_losses >= limits.max_consecutive_losses:
        breach_reason = f"Consecutive losses ({consecutive_losses}) breached limit of {limits.max_consecutive_losses} — possible revenge-trading pattern"
    elif limits.max_total_trades is not None and total_trades_this_window >= limits.max_total_trades:
        breach_reason = f"Total trades this window ({total_trades_this_window}) breached ceiling of {limits.max_total_trades}"

    if breach_reason:
        _halt(breach_reason, metrics)
        return {"halted": True, "reason": breach_reason, "newly_halted": True, "metrics": metrics}

    return {"halted": False, "metrics": metrics}


def get_event_history(limit: int = 50) -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM kill_switch_events ORDER BY created_at DESC LIMIT %s", (limit,)
            )
            return [dict(r) for r in cur.fetchall()]


if __name__ == "__main__":
    init_schema()
    print("kill_switch schema ready.")
    print("Call check_kill_switch() before every paper-trading decision.")
