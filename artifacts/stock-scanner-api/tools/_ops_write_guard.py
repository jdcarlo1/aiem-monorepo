"""
Precondition gate for future bulk-rewrite scripts. No caller exists as of
2026-07-30 — do not wire to grade_options_outcomes() or any per-row write path.

Internal write guard for aiem_options_alerts and aiem_options_alert_snapshots.

Any script that bulk-updates existing rows in these tables must call
require_snapshot_rewrite_authorization() before touching the DB.

The check is runtime-only: set the environment variable
  OPTIONS_SNAPSHOT_REWRITE_AUTHORIZED=1
explicitly before running. It is never set in deployed code.

All attempts (authorized or blocked) are logged to _ops_snapshot_write_attempts.
"""

import os
import psycopg2


_AUTHORIZED_VALUE = "1"
_ENV_KEY = "OPTIONS_SNAPSHOT_REWRITE_AUTHORIZED"

_PROTECTED_TABLES = frozenset([
    "aiem_options_alerts",
    "aiem_options_alert_snapshots",
])


def _log_attempt(db_url: str, caller: str, table: str, authorized: bool, notes: str = "") -> None:
    """Write an attempt record regardless of authorization outcome."""
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO _ops_snapshot_write_attempts
                           (caller, table_name, authorized, row_count, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (caller, table, authorized, None, notes[:2000] if notes else ""),
                )
    except Exception:
        # Log failure must never crash the caller
        pass


def require_snapshot_rewrite_authorization(
    caller: str,
    table: str = "aiem_options_alert_snapshots",
    db_url: str | None = None,
) -> None:
    """
    Raise RuntimeError unless OPTIONS_SNAPSHOT_REWRITE_AUTHORIZED=1 is set
    in the process environment at the moment of the call.

    Logs the attempt either way to _ops_snapshot_write_attempts.

    Args:
        caller:  Short description of the calling script / function.
        table:   Target table name (informational; used in log only).
        db_url:  Override DATABASE_URL (uses env var if None).
    """
    if table not in _PROTECTED_TABLES:
        # Not a protected table; skip guard silently.
        return

    url = db_url or os.environ.get("DATABASE_URL", "")
    authorized = os.environ.get(_ENV_KEY, "").strip() == _AUTHORIZED_VALUE

    _log_attempt(
        db_url=url,
        caller=caller,
        table=table,
        authorized=authorized,
        notes="" if authorized else f"Blocked: {_ENV_KEY} not set to '{_AUTHORIZED_VALUE}'",
    )

    if not authorized:
        raise RuntimeError(
            f"Bulk rewrite of '{table}' blocked. "
            f"Set {_ENV_KEY}=1 in the environment to authorize. "
            f"Caller: {caller}"
        )
