"""
aiem_pipeline_checkpoints.py — Atomic per-stage DB checkpoint helper.

Called from:
  aiem_telegram_notifier.py   (stages 1-3, watchdog loop)
  aiem_process.py             (stages 4-6, /run-scan handler + _startup_full_catchup)
  aiem_options_scheduler.py   (stages 7-11, seed_daily_candidates + _execute_job)

Hard requirements enforced here:
  - Atomic: each write_checkpoint() is its OWN committed psycopg2 transaction.
  - Idempotent: ON CONFLICT(trace_id, stage) DO UPDATE — retries safe.
  - Alert-on-failure: chk() logs ERROR and optionally calls alert_fn — NEVER silent.
  - Write-before-work: callers MUST call chk() before the stage logic executes.
"""

import json
import logging
import uuid as _uuid_mod

import psycopg2

log = logging.getLogger(__name__)

STAGE_ORDER: dict = {
    "WATCHDOG_POLL":     1,
    "RUN_SCAN_CALLED":   2,
    "RUN_SCAN_RESPONSE": 3,
    "TRIGGER_EVALUATED": 4,
    "TRIGGER_LOGGED":    5,
    "SCAN_RUN_CREATED":  6,
    "SEED_STAGE":        7,
    "P2_INIT":           8,
    "P2_GATE":           9,
    "P2_CAPTURE":       10,
    "DECISION_WRITTEN": 11,
}

_DDL_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS pipeline_stage_checkpoints (
    id          BIGSERIAL    PRIMARY KEY,
    trace_id    TEXT         NOT NULL,
    stage       TEXT         NOT NULL,
    stage_order INT          NOT NULL,
    payload     JSONB,
    written_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT psc_trace_stage_uq UNIQUE (trace_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_psc_trace_id
    ON pipeline_stage_checkpoints (trace_id);
"""

_DDL_TRACE_CTX = """
CREATE TABLE IF NOT EXISTS pipeline_trace_context (
    scan_date   DATE         PRIMARY KEY,
    trace_id    TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);
"""

_INS_CHECKPOINT = """
INSERT INTO pipeline_stage_checkpoints
    (trace_id, stage, stage_order, payload, written_at)
VALUES (%s, %s, %s, %s::jsonb, NOW())
ON CONFLICT (trace_id, stage) DO UPDATE
    SET payload    = EXCLUDED.payload,
        written_at = NOW()
"""

_TABLES_ENSURED: set = set()


def ensure_tables(db_url: str) -> None:
    """Idempotent CREATE IF NOT EXISTS — cheap after first call (set-guarded)."""
    if db_url in _TABLES_ENSURED:
        return
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(_DDL_CHECKPOINTS)
            cur.execute(_DDL_TRACE_CTX)
            conn.commit()
        _TABLES_ENSURED.add(db_url)
    except Exception as e:
        log.warning(f"[checkpoint] ensure_tables non-fatal: {e}")


def write_checkpoint(
    trace_id: str,
    stage: str,
    payload=None,
    db_url: str = "",
) -> None:
    """
    Atomic committed write for one checkpoint row.  RAISES on DB failure.
    Idempotent: ON CONFLICT(trace_id, stage) DO UPDATE.
    """
    order = STAGE_ORDER.get(stage, 99)
    payload_json = json.dumps(payload) if payload is not None else None
    with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(_INS_CHECKPOINT, (trace_id, stage, order, payload_json))
        conn.commit()


def chk(
    trace_id,
    stage: str,
    payload,
    db_url: str,
    alert_fn=None,
) -> None:
    """
    Safe wrapper: logs ERROR and optionally alerts on failure.  Never raises.
    Checkpoint write failure is surfaced (not swallowed) but does not halt the pipeline.
    Callers MUST invoke this BEFORE the stage logic they are checkpointing.
    """
    if not trace_id:
        return
    try:
        write_checkpoint(trace_id, stage, payload, db_url)
    except Exception as e:
        msg = f"[CHECKPOINT WRITE FAILED] stage={stage} trace={str(trace_id)[:8]}: {e}"
        log.error(msg)
        if alert_fn:
            try:
                alert_fn(f"\u26a0\ufe0f {msg}")
            except Exception:
                pass


def get_or_set_trace_id(scan_date, db_url: str, new_trace_id: str = None) -> str:
    """
    Read today's trace_id from pipeline_trace_context.
    If not found: write new_trace_id (or a fresh UUID) and return it.
    Always returns a non-empty string — never raises.
    """
    fresh = new_trace_id or str(_uuid_mod.uuid4())
    try:
        ensure_tables(db_url)
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pipeline_trace_context (scan_date, trace_id)
                VALUES (%s, %s)
                ON CONFLICT (scan_date) DO NOTHING
            """, (scan_date, fresh))
            conn.commit()
            cur.execute(
                "SELECT trace_id FROM pipeline_trace_context WHERE scan_date=%s",
                (scan_date,))
            row = cur.fetchone()
            return row[0] if row else fresh
    except Exception as e:
        log.warning(f"[checkpoint] get_or_set_trace_id failed: {e}")
        return fresh
