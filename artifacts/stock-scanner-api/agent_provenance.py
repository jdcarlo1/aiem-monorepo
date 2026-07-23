"""
agent_provenance.py — Write-provenance logging for AIEM agent DB writes.

Every INSERT/UPDATE/DELETE executed by the agent must call log_write().
Writes that lack an active instruction context are auto-flagged with
flag_reason='no_instruction_context' — the negative-control invariant.

Usage:
    import agent_provenance as prov

    # At the start of a code path that was instructed by a chat message:
    prov.set_instruction_context(
        session_id="8530e9e7-59ef-4bc2-8765-e5fc093a2462",
        instruction_ts="2026-07-23T14:00:00Z",
        instruction_seq=42,          # monotonic counter within session
    )

    # Before each write:
    prov.log_write(conn, "aiem_options_alert_snapshots", "INSERT", [25])

    # At the end of the instructed block:
    prov.clear_instruction_context()
"""

import json
import os
import threading
from typing import Optional

_REPLIT_SESSION_ID: str = os.environ.get(
    "REPLIT_AGENT_SESSION_ID", "unknown-session"
)

_ctx = threading.local()


def set_instruction_context(
    session_id: str,
    instruction_ts: str,
    instruction_seq: int,
) -> None:
    _ctx.session_id = session_id
    _ctx.instruction_ts = instruction_ts
    _ctx.instruction_seq = instruction_seq


def clear_instruction_context() -> None:
    _ctx.session_id = None
    _ctx.instruction_ts = None
    _ctx.instruction_seq = None


def _active_ctx() -> dict:
    return {
        "session_id": getattr(_ctx, "session_id", None) or _REPLIT_SESSION_ID,
        "instruction_ts": getattr(_ctx, "instruction_ts", None),
        "instruction_seq": getattr(_ctx, "instruction_seq", None),
    }


def log_write(
    conn,
    table_name: str,
    operation: str,
    affected_ids=None,
    actor: str = "aiem_agent",
) -> int:
    """
    Log one DB write to agent_write_provenance.

    Returns the inserted row id, or -1 on failure (never raises — callers must
    not lose their own write just because provenance logging failed).

    If no instruction context is active, the row is written with
    flagged=True, flag_reason='no_instruction_context'.
    """
    ctx = _active_ctx()
    flagged = ctx["instruction_ts"] is None
    flag_reason = "no_instruction_context" if flagged else None

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_write_provenance
                    (session_id, actor, instruction_ts, instruction_seq,
                     table_name, operation, affected_ids, flagged, flag_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    ctx["session_id"],
                    actor,
                    ctx["instruction_ts"],
                    ctx["instruction_seq"],
                    table_name,
                    operation,
                    json.dumps(affected_ids, default=str) if affected_ids is not None else None,
                    flagged,
                    flag_reason,
                ),
            )
            row_id = cur.fetchone()[0]
        return row_id
    except Exception as exc:
        print(f"[agent_provenance] log_write failed (non-fatal): {exc}")
        return -1


def ensure_schema(conn) -> None:
    """Idempotent — safe to call on every startup."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_write_provenance (
                id              BIGSERIAL PRIMARY KEY,
                session_id      TEXT NOT NULL,
                actor           TEXT NOT NULL DEFAULT 'aiem_agent',
                instruction_ts  TIMESTAMPTZ,
                instruction_seq INT,
                table_name      TEXT NOT NULL,
                operation       TEXT NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE')),
                affected_ids    JSONB,
                flagged         BOOLEAN NOT NULL DEFAULT FALSE,
                flag_reason     TEXT,
                written_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS awp_session ON agent_write_provenance(session_id, written_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS awp_flagged ON agent_write_provenance(flagged) WHERE flagged=TRUE"
        )
    conn.commit()


def negative_control_test(conn) -> dict:
    """
    Negative-control test: perform a write with NO active instruction context.
    Asserts the row is automatically flagged.
    Returns {'result': 'PASS'|'FAIL', 'row_id': int, 'flagged': bool, 'flag_reason': str}.
    """
    clear_instruction_context()

    row_id = log_write(conn, "_negctl_test", "INSERT", ["negctl-probe"])
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT flagged, flag_reason FROM agent_write_provenance WHERE id=%s",
            (row_id,),
        )
        row = cur.fetchone()

    flagged = row[0] if row else None
    flag_reason = row[1] if row else None

    result = "PASS" if flagged is True and flag_reason == "no_instruction_context" else "FAIL"
    return {
        "result": result,
        "row_id": row_id,
        "flagged": flagged,
        "flag_reason": flag_reason,
    }


def log_credential_usage(conn, credential_name: str, session_id: Optional[str] = None) -> None:
    """
    Record that credential `credential_name` was used to open a connection.
    Call this immediately after psycopg2.connect() using that credential.
    Provides the last-used timestamp per credential that server-level logs cannot.
    Never raises — connection failure to log must not break the caller.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            db_user = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO credential_usage_log (credential_name, db_user, session_id)
                VALUES (%s, %s, %s)
                """,
                (credential_name, db_user, session_id or _REPLIT_SESSION_ID),
            )
    except Exception as exc:
        print(f"[agent_provenance] log_credential_usage failed (non-fatal): {exc}")


def positive_control_test(conn, session_id: str) -> dict:
    """
    Positive-control test: write WITH an active instruction context.
    Asserts the row is NOT flagged.
    """
    set_instruction_context(
        session_id=session_id,
        instruction_ts="2026-07-23T00:00:00Z",
        instruction_seq=0,
    )
    row_id = log_write(conn, "_posctl_test", "INSERT", ["posctl-probe"])
    conn.commit()
    clear_instruction_context()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT flagged, flag_reason, instruction_ts, instruction_seq FROM agent_write_provenance WHERE id=%s",
            (row_id,),
        )
        row = cur.fetchone()

    flagged = row[0] if row else None
    result = "PASS" if flagged is False else "FAIL"
    return {
        "result": result,
        "row_id": row_id,
        "flagged": flagged,
        "flag_reason": row[1] if row else None,
        "instruction_ts": str(row[2]) if row else None,
        "instruction_seq": row[3] if row else None,
    }
