"""
Module 4 — Human Approval Gate
================================
Translates Module 2 decay verdicts (and future Module 3 promotion recommendations)
into status changes in aiem_signal_discoveries, gated by explicit human confirmation
via the admin API.

Design invariants:
  - This module NEVER changes signal status automatically.
  - Every status transition requires a POST to /stock-api/admin/module4-approve.
  - All actions are logged to aiem_signal_actions with a full audit trail.
  - A signal disappears from the pending list once acted on, because its db_status
    no longer matches the 'unfixed' state Module 2 recorded.
  - Telegram notification is handled by the calling endpoint in main.py (not here),
    keeping this module free of external service dependencies.

Valid actions:
  retire    — moves signal to 'retired'   (for 'failing' or 'decaying' verdicts)
  downgrade — moves signal to 'hypothesis' (for 'decaying' verdicts on validated signals)
  keep      — no status change; logs that human reviewed and chose to hold
  promote   — moves signal to 'validated'  (for Module 3 promotion recommendations)
"""

import datetime as _dt
import typing as _t

_VALID_ACTIONS = frozenset({"retire", "keep", "downgrade", "promote"})

_ACTION_TO_STATUS: dict[str, str | None] = {
    "retire":    "retired",
    "downgrade": "hypothesis",
    "keep":      None,
    "promote":   "validated",
}

_VERDICT_RECOMMENDED_ACTION: dict[str, str] = {
    "failing":  "retire",
    "decaying": "downgrade",
    "holding":  "keep",
}


# ---------------------------------------------------------------------------
# Schema init

def init_schema(conn) -> None:
    """Create aiem_signal_actions table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_signal_actions (
                id               BIGSERIAL PRIMARY KEY,
                discovery_id     INT NOT NULL,
                action           TEXT NOT NULL,
                from_status      TEXT NOT NULL,
                to_status        TEXT,
                decay_verdict    TEXT,
                realized_n       INT,
                realized_win_rate DOUBLE PRECISION,
                reason           TEXT,
                approved_by      TEXT NOT NULL DEFAULT 'admin',
                approved_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_actions_disc
            ON aiem_signal_actions (discovery_id, approved_at DESC)
        """)
    conn.commit()


# ---------------------------------------------------------------------------
# Pending actions

def get_pending_actions(conn) -> list[dict]:
    """
    Return signals that have an actionable Module 2 verdict (failing / decaying)
    and have not yet been acted on since the last Module 2 run.

    A signal is considered "acted on" if aiem_signal_actions has a row for it
    with approved_at > the Module 2 run_at timestamp.  Once the status has been
    changed, the signal naturally drops out because it will re-appear in Module 2
    under its new status on the next weekly run.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                m2.discovery_id,
                m2.evaluation_status,
                m2.decay_verdict,
                m2.realized_n,
                m2.realized_win_rate,
                m2.realized_p_value,
                m2.win_rate_at_discovery,
                m2.delta_vs_discovery_pp,
                m2.forward_days_accumulated,
                m2.run_at,
                d.status           AS current_status,
                d.hypothesis_text,
                d.horizon,
                d.signal_win_rate  AS disc_win_rate,
                d.signal_n         AS disc_n,
                a.action           AS last_action,
                a.approved_at      AS last_action_at
            FROM aiem_module2_evaluations m2
            JOIN aiem_signal_discoveries d ON d.id = m2.discovery_id
            LEFT JOIN LATERAL (
                SELECT action, approved_at
                FROM aiem_signal_actions
                WHERE discovery_id = m2.discovery_id
                  AND approved_at > m2.run_at
                ORDER BY approved_at DESC
                LIMIT 1
            ) a ON TRUE
            WHERE m2.evaluation_status = 'evaluable_now'
              AND m2.decay_verdict IN ('failing', 'decaying')
              AND a.action IS NULL
            ORDER BY m2.decay_verdict DESC, m2.discovery_id
        """)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    result = []
    for row in rows:
        r = dict(zip(cols, row))
        r["recommended_action"]    = _VERDICT_RECOMMENDED_ACTION.get(
            r.get("decay_verdict") or "", "keep"
        )
        r["recommended_to_status"] = _ACTION_TO_STATUS.get(
            r["recommended_action"]
        )
        for k in ("run_at", "last_action_at"):
            if r.get(k):
                r[k] = r[k].isoformat()
        result.append(r)
    return result


# ---------------------------------------------------------------------------
# Apply action

def apply_action(
    conn,
    discovery_id: int,
    action: str,
    reason: str,
    approved_by: str = "admin",
) -> dict:
    """
    Apply a human-approved action to a signal discovery.

    Returns a result dict with full audit information.
    Raises ValueError for invalid inputs or illegal state transitions.
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(
            f"Invalid action '{action}'. Must be one of {sorted(_VALID_ACTIONS)}"
        )
    if not reason or not reason.strip():
        raise ValueError("reason is required and must be non-empty")

    to_status = _ACTION_TO_STATUS[action]

    with conn.cursor() as cur:
        # Fetch current signal state and the latest Module 2 verdict
        cur.execute("""
            SELECT
                d.id, d.status, d.hypothesis_text,
                m2.decay_verdict,
                m2.realized_n,
                m2.realized_win_rate,
                m2.realized_p_value,
                m2.evaluation_status
            FROM aiem_signal_discoveries d
            LEFT JOIN aiem_module2_evaluations m2 ON m2.discovery_id = d.id
            WHERE d.id = %s
        """, (discovery_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"discovery_id={discovery_id} not found in aiem_signal_discoveries")

        (disc_id, from_status, hyp_text,
         decay_verdict, realized_n, realized_wr, realized_p, eval_status) = row

        # Guard: 'keep' is always allowed; other actions require an evaluable_now signal
        if action != "keep" and eval_status != "evaluable_now":
            raise ValueError(
                f"Cannot apply action '{action}' to id={disc_id}: "
                f"evaluation_status='{eval_status}' (need 'evaluable_now')"
            )

        # Guard: don't apply a transition that is already in place
        if to_status is not None and from_status == to_status:
            raise ValueError(
                f"Signal id={disc_id} is already in status='{to_status}'; "
                f"action '{action}' is a no-op"
            )

        # Apply status change
        if to_status is not None:
            cur.execute("""
                UPDATE aiem_signal_discoveries
                SET status = %s
                WHERE id = %s
            """, (to_status, disc_id))

        # Log the action
        cur.execute("""
            INSERT INTO aiem_signal_actions
                (discovery_id, action, from_status, to_status,
                 decay_verdict, realized_n, realized_win_rate,
                 reason, approved_by, approved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id, approved_at
        """, (
            disc_id, action, from_status, to_status,
            decay_verdict, realized_n, realized_wr,
            reason.strip(), approved_by,
        ))
        action_id, approved_at = cur.fetchone()

    conn.commit()

    return {
        "action_id":               action_id,
        "discovery_id":            disc_id,
        "action":                  action,
        "from_status":             from_status,
        "to_status":               to_status,
        "status_changed":          (to_status is not None and to_status != from_status),
        "decay_verdict":           decay_verdict,
        "realized_n":              realized_n,
        "realized_win_rate":       realized_wr,
        "realized_p_value":        realized_p,
        "reason":                  reason.strip(),
        "approved_by":             approved_by,
        "approved_at":             approved_at.isoformat() if approved_at else None,
        "hypothesis_text_snippet": (hyp_text or "")[:120],
    }


# ---------------------------------------------------------------------------
# Action history

def get_action_history(
    conn,
    discovery_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return recent action history, optionally filtered by discovery_id."""
    limit = min(max(int(limit), 1), 200)
    with conn.cursor() as cur:
        if discovery_id is not None:
            cur.execute("""
                SELECT a.id, a.discovery_id, a.action, a.from_status,
                       a.to_status, a.decay_verdict, a.realized_n,
                       a.realized_win_rate, a.reason, a.approved_by,
                       a.approved_at, d.hypothesis_text
                FROM aiem_signal_actions a
                JOIN aiem_signal_discoveries d ON d.id = a.discovery_id
                WHERE a.discovery_id = %s
                ORDER BY a.approved_at DESC
                LIMIT %s
            """, (discovery_id, limit))
        else:
            cur.execute("""
                SELECT a.id, a.discovery_id, a.action, a.from_status,
                       a.to_status, a.decay_verdict, a.realized_n,
                       a.realized_win_rate, a.reason, a.approved_by,
                       a.approved_at, d.hypothesis_text
                FROM aiem_signal_actions a
                JOIN aiem_signal_discoveries d ON d.id = a.discovery_id
                ORDER BY a.approved_at DESC
                LIMIT %s
            """, (limit,))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

    result = []
    for row in rows:
        r = dict(zip(cols, row))
        if r.get("approved_at"):
            r["approved_at"] = r["approved_at"].isoformat()
        result.append(r)
    return result
