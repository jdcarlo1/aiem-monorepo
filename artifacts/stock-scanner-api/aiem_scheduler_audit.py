"""
aiem_scheduler_audit.py — Scheduler run audit for the 9:42 AM paper-trading job.

Creates and writes to scheduler_run_audit, which tracks every scheduled
paper-trading run outcome with the five fields required by D13 final
reliability verification:
  - scheduled_time    : the 9:42 AM ET time slot for this trading day
  - actual_start_time : when the run actually began (None for SKIPPED)
  - status            : EXECUTED | RECOVERED | SKIPPED
  - reason            : human-readable explanation of the status
  - trace_id          : cross-reference to aiem_paper_execution_log.id

Status semantics:
  EXECUTED  — the 9:42 AM CronTrigger fired normally (trigger_source=scheduled_942
              or admin_run_paper_today).
  RECOVERED — the server was offline at 9:42 AM; startup_catchup detected the
              miss and replayed the run before 4 PM ET (trigger_source=startup_catchup).
  SKIPPED   — the server restarted after 4 PM ET; the 9:42 AM window has closed
              and replay would be unsafe (stale data, end-of-day positions).
              No trades are placed; the miss is recorded explicitly.

Import is lazy (try/except) everywhere it is called — a missing/broken module
must NEVER affect trade flow.
"""

import psycopg2

_SCHEMA_ENSURED = False  # module-level flag; ensures schema once per process lifetime

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scheduler_run_audit (
    id                BIGSERIAL PRIMARY KEY,
    scheduled_time    TIMESTAMPTZ NOT NULL,
    actual_start_time TIMESTAMPTZ,
    status            TEXT        NOT NULL
                      CHECK (status IN ('EXECUTED', 'RECOVERED', 'SKIPPED')),
    reason            TEXT,
    trigger_source    TEXT,
    exec_log_id       INTEGER     REFERENCES aiem_paper_execution_log(id)
                                  ON DELETE SET NULL,
    trace_id          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sra_scheduled_time
    ON scheduler_run_audit (scheduled_time DESC);
CREATE INDEX IF NOT EXISTS idx_sra_status
    ON scheduler_run_audit (status, created_at DESC);
"""


def ensure_schema(db_url: str) -> None:
    """Idempotent — safe to call on every process boot."""
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE)
            conn.commit()
        print("[scheduler_audit] schema ready — scheduler_run_audit table OK")
    except Exception as exc:
        print(f"[scheduler_audit] schema init error (non-fatal): {exc}")


def write_audit(
    db_url: str,
    scheduled_time,          # datetime with tzinfo (9:42 AM ET for this trading day)
    actual_start_time,       # datetime with tzinfo, or None for SKIPPED
    status: str,             # 'EXECUTED' | 'RECOVERED' | 'SKIPPED'
    reason: str,
    trigger_source: str = None,
    exec_log_id: int = None,
    trace_id: str = None,
) -> int | None:
    """
    Insert one row into scheduler_run_audit.  Returns the new row id, or None
    on error.  Never raises — caller must not crash if this fails.
    """
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO scheduler_run_audit
                        (scheduled_time, actual_start_time, status, reason,
                         trigger_source, exec_log_id, trace_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        scheduled_time,
                        actual_start_time,
                        status,
                        reason,
                        trigger_source,
                        exec_log_id,
                        trace_id,
                    ),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
        print(
            f"[scheduler_audit] id={row_id} status={status} "
            f"trigger={trigger_source} sched={scheduled_time} "
            f"exec_log={exec_log_id} trace={trace_id}"
        )
        return row_id
    except Exception as exc:
        print(f"[scheduler_audit] write_audit error (non-fatal): {exc}")
        return None


def get_todays_audit(db_url: str, trade_date) -> list:
    """
    Return all audit rows for a given trade_date (date object).
    Used by the simulation script and admin endpoints.
    """
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, scheduled_time, actual_start_time, status,
                           reason, trigger_source, exec_log_id, trace_id, created_at
                    FROM scheduler_run_audit
                    WHERE DATE(scheduled_time AT TIME ZONE 'America/New_York') = %s
                    ORDER BY id ASC
                    """,
                    (trade_date,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        print(f"[scheduler_audit] get_todays_audit error: {exc}")
        return []
