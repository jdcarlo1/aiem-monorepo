"""
scheduler_trace.py — Causal chain capture for the options pipeline scheduler.

Records every stage of the pipeline execution as an immutable row in
oe_scheduler_trace, all sharing the same trace_id. This satisfies the
R8 audit directive Item 8 requirement for machine-generated scheduler evidence.

Causal chain captured:
  SCHEDULER_FIRE → JOB_CLAIM → MARKET_DATA_CAPTURE → ANALYSIS →
  PROBABILITY → PORTFOLIO_RISK → RISK_GATE → DECISION →
  REPLAY_INPUT_CAPTURE → AUDIT_RECORD → PAPER_EXECUTION_OR_NO_TRADE →
  OUTCOME_TRACKING

Each write is best-effort (non-fatal) — a capture failure must never block
the pipeline. Fatal errors are logged at WARNING level.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("scheduler_trace")

# Stage definitions — fixed sequence for causal chain validation
STAGES = [
    "SCHEDULER_FIRE",
    "JOB_CLAIM",
    "MARKET_DATA_CAPTURE",
    "ANALYSIS",
    "PROBABILITY",
    "PORTFOLIO_RISK",
    "RISK_GATE",
    "DECISION",
    "REPLAY_INPUT_CAPTURE",
    "AUDIT_RECORD",
    "PAPER_EXECUTION_OR_NO_TRADE",
    "OUTCOME_TRACKING",
]
STAGE_SEQ = {s: i + 1 for i, s in enumerate(STAGES)}

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS oe_scheduler_trace (
    id                  BIGSERIAL PRIMARY KEY,
    trace_id            VARCHAR(64)  NOT NULL,
    stage_name          VARCHAR(64)  NOT NULL,
    stage_seq           INTEGER      NOT NULL,
    recorded_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ticker              VARCHAR(16),
    scan_date           DATE,
    scheduler_name      VARCHAR(128),
    scheduler_impl      VARCHAR(128),
    scheduler_timezone  VARCHAR(64),
    cron_expression     VARCHAR(256),
    next_run_time       TIMESTAMPTZ,
    fire_timestamp      TIMESTAMPTZ,
    worker_identity     VARCHAR(256),
    worker_boot_id      VARCHAR(128),
    worker_pid          INTEGER,
    job_id              INTEGER,
    job_claim_timestamp TIMESTAMPTZ,
    unique_run_id       VARCHAR(64),
    origin_type         VARCHAR(32)  DEFAULT 'SCHEDULER',
    decision_id         VARCHAR(64),
    alert_id            INTEGER,
    completion_status   VARCHAR(32),
    retry_count         INTEGER      DEFAULT 0,
    duplicate_count     INTEGER      DEFAULT 0,
    failure_reason      TEXT,
    stage_metadata      JSONB,
    is_test_record      BOOLEAN      NOT NULL DEFAULT FALSE,
    CONSTRAINT oe_sched_trace_stage_check CHECK (
        stage_name IN (
            'SCHEDULER_FIRE','JOB_CLAIM','MARKET_DATA_CAPTURE',
            'ANALYSIS','PROBABILITY','PORTFOLIO_RISK','RISK_GATE',
            'DECISION','REPLAY_INPUT_CAPTURE','AUDIT_RECORD',
            'PAPER_EXECUTION_OR_NO_TRADE','OUTCOME_TRACKING'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_oe_sched_trace_trace_id
    ON oe_scheduler_trace(trace_id);
CREATE INDEX IF NOT EXISTS idx_oe_sched_trace_recorded_at
    ON oe_scheduler_trace(recorded_at DESC);

-- Immutability trigger: production rows cannot be updated or deleted
CREATE OR REPLACE FUNCTION trg_fn_oe_sched_trace_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.is_test_record = FALSE THEN
        RAISE EXCEPTION '[DPL] oe_scheduler_trace production rows are immutable '
                        '(is_test_record = FALSE)';
    END IF;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_oe_sched_trace_immutable ON oe_scheduler_trace;
CREATE TRIGGER trg_oe_sched_trace_immutable
    BEFORE UPDATE OR DELETE ON oe_scheduler_trace
    FOR EACH ROW EXECUTE FUNCTION trg_fn_oe_sched_trace_immutable();

-- Scheduler config view for evidence queries
CREATE TABLE IF NOT EXISTS oe_scheduler_config_log (
    id              BIGSERIAL    PRIMARY KEY,
    recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    scheduler_name  VARCHAR(128) NOT NULL,
    scheduler_impl  VARCHAR(128),
    timezone        VARCHAR(64),
    cron_expression VARCHAR(256),
    next_run_time   TIMESTAMPTZ,
    worker_identity VARCHAR(256),
    worker_pid      INTEGER,
    boot_id         VARCHAR(128),
    config_metadata JSONB
);
"""


def bootstrap(db_url: str) -> None:
    """Idempotent: create oe_scheduler_trace and related tables."""
    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor() as cur:
            cur.execute(_BOOTSTRAP_SQL)
            conn.commit()
        log.info("[scheduler_trace] bootstrap complete")
    except Exception as e:
        log.warning(f"[scheduler_trace] bootstrap failed (non-fatal): {e}")


def _get_worker_boot_id() -> str:
    """Platform boot ID — stable for the lifetime of the OS session."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except Exception:
        pass
    try:
        import subprocess
        r = subprocess.run(["sysctl", "-n", "kern.bootsessionuuid"],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return f"unknown-{socket.gethostname()}"


_BOOT_ID: str = _get_worker_boot_id()
_WORKER_IDENTITY: str = f"{socket.gethostname()}|pid={os.getpid()}"


@dataclass
class TraceContext:
    """
    Holds scheduler-level context shared across all stage writes for one pipeline run.
    Created once per scheduler fire event, passed through _execute_job.
    """
    trace_id:          str
    scheduler_name:    str    = "aiem_options_scheduler"
    scheduler_impl:    str    = "APScheduler-BackgroundScheduler"
    scheduler_timezone: str   = "America/New_York"
    cron_expression:   str    = "0 9 45 * * MON-FRI"
    next_run_time:     str | None = None
    fire_timestamp:    str | None = None
    worker_identity:   str    = field(default_factory=lambda: _WORKER_IDENTITY)
    worker_boot_id:    str    = field(default_factory=lambda: _BOOT_ID)
    worker_pid:        int    = field(default_factory=os.getpid)
    unique_run_id:     str    = field(default_factory=lambda: str(uuid.uuid4()))
    origin_type:       str    = "SCHEDULER"
    db_url:            str    = ""

    def write_stage(
        self,
        stage_name: str,
        ticker: str | None = None,
        scan_date: Any = None,
        job_id: int | None = None,
        job_claim_timestamp: str | None = None,
        decision_id: str | None = None,
        alert_id: int | None = None,
        completion_status: str | None = None,
        retry_count: int = 0,
        duplicate_count: int = 0,
        failure_reason: str | None = None,
        metadata: dict | None = None,
        is_test_record: bool = False,
    ) -> None:
        """
        Append one stage row to oe_scheduler_trace. Non-fatal.
        """
        if stage_name not in STAGE_SEQ:
            log.warning(f"[scheduler_trace] unknown stage {stage_name!r} — skipping")
            return
        try:
            import psycopg2
            import psycopg2.extras
            with psycopg2.connect(self.db_url, connect_timeout=4) as conn, \
                 conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO oe_scheduler_trace (
                        trace_id, stage_name, stage_seq, ticker, scan_date,
                        scheduler_name, scheduler_impl, scheduler_timezone,
                        cron_expression, next_run_time, fire_timestamp,
                        worker_identity, worker_boot_id, worker_pid,
                        job_id, job_claim_timestamp, unique_run_id,
                        origin_type, decision_id, alert_id,
                        completion_status, retry_count, duplicate_count,
                        failure_reason, stage_metadata, is_test_record
                    ) VALUES (
                        %s,%s,%s,%s,%s,
                        %s,%s,%s,
                        %s,%s,%s,
                        %s,%s,%s,
                        %s,%s,%s,
                        %s,%s,%s,
                        %s,%s,%s,
                        %s,%s,%s
                    )
                """, (
                    self.trace_id, stage_name, STAGE_SEQ[stage_name],
                    ticker, scan_date,
                    self.scheduler_name, self.scheduler_impl, self.scheduler_timezone,
                    self.cron_expression, self.next_run_time, self.fire_timestamp,
                    self.worker_identity, self.worker_boot_id, self.worker_pid,
                    job_id, job_claim_timestamp, self.unique_run_id,
                    self.origin_type, decision_id, alert_id,
                    completion_status, retry_count, duplicate_count,
                    failure_reason,
                    psycopg2.extras.Json(metadata or {}),
                    is_test_record,
                ))
                conn.commit()
            log.debug(f"[scheduler_trace] stage={stage_name} trace_id={self.trace_id} "
                      f"ticker={ticker}")
        except Exception as e:
            log.warning(f"[scheduler_trace] write_stage {stage_name} failed "
                        f"(non-fatal): {e}")


def make_batch_trace_id(scan_date: Any, fire_ts: str) -> str:
    """
    Deterministic trace_id for the scheduler FIRE event (batch level, not per-job).
    Per-job trace_id is computed from ticker+scan_date+claim_id in _execute_job.
    """
    raw = f"SCHEDULER_FIRE:{scan_date}:{fire_ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def log_scheduler_config(
    db_url: str,
    scheduler_name: str,
    scheduler_impl: str,
    timezone: str,
    cron_expression: str,
    next_run_time: Any,
    metadata: dict | None = None,
) -> None:
    """
    Record the scheduler's current configuration. Called once at startup
    and whenever the scheduler is reconfigured.
    """
    try:
        import psycopg2
        import psycopg2.extras
        with psycopg2.connect(db_url, connect_timeout=4) as conn, \
             conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_scheduler_config_log (
                    scheduler_name, scheduler_impl, timezone, cron_expression,
                    next_run_time, worker_identity, worker_pid, boot_id, config_metadata
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                scheduler_name, scheduler_impl, timezone, cron_expression,
                next_run_time, _WORKER_IDENTITY, os.getpid(), _BOOT_ID,
                psycopg2.extras.Json(metadata or {}),
            ))
            conn.commit()
    except Exception as e:
        log.warning(f"[scheduler_trace] log_scheduler_config failed (non-fatal): {e}")


def get_stage_evidence(trace_id: str, db_url: str) -> list[dict]:
    """
    Retrieve all stage rows for a trace_id, ordered by stage_seq.
    Used by the verifier to build the causal chain report.
    """
    try:
        import psycopg2
        import psycopg2.extras
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT trace_id, stage_name, stage_seq, recorded_at,
                       ticker, scan_date, scheduler_name, scheduler_impl,
                       scheduler_timezone, cron_expression, next_run_time,
                       fire_timestamp, worker_identity, worker_boot_id,
                       worker_pid, job_id, job_claim_timestamp, unique_run_id,
                       origin_type, decision_id, alert_id,
                       completion_status, retry_count, duplicate_count,
                       failure_reason, stage_metadata
                FROM oe_scheduler_trace
                WHERE trace_id = %s AND is_test_record = FALSE
                ORDER BY stage_seq ASC, recorded_at ASC
            """, (trace_id,))
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.warning(f"[scheduler_trace] get_stage_evidence failed: {e}")
        return []


def get_latest_complete_trace(db_url: str) -> dict | None:
    """
    Find the most recent trace_id that has a SCHEDULER_FIRE stage.
    Returns a summary dict or None.
    """
    try:
        import psycopg2
        import psycopg2.extras
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT t.trace_id,
                       MIN(t.recorded_at) AS first_stage_at,
                       MAX(t.recorded_at) AS last_stage_at,
                       COUNT(*)           AS stage_count,
                       MAX(t.fire_timestamp) AS fire_timestamp,
                       MAX(t.ticker)     AS last_ticker,
                       MAX(t.decision_id) AS last_decision_id,
                       MAX(t.alert_id)   AS last_alert_id,
                       MAX(t.cron_expression) AS cron_expression,
                       MAX(t.scheduler_timezone) AS timezone,
                       MAX(t.worker_identity) AS worker_identity,
                       MAX(t.unique_run_id) AS unique_run_id,
                       bool_or(t.stage_name = 'SCHEDULER_FIRE')  AS has_fire,
                       bool_or(t.stage_name = 'JOB_CLAIM')       AS has_claim,
                       bool_or(t.stage_name = 'DECISION')        AS has_decision,
                       bool_or(t.stage_name = 'REPLAY_INPUT_CAPTURE') AS has_replay,
                       bool_or(t.stage_name = 'AUDIT_RECORD')    AS has_audit,
                       bool_or(t.stage_name = 'PAPER_EXECUTION_OR_NO_TRADE') AS has_paper,
                       bool_or(t.stage_name = 'OUTCOME_TRACKING') AS has_outcome
                FROM oe_scheduler_trace t
                WHERE t.is_test_record = FALSE
                GROUP BY t.trace_id
                HAVING bool_or(t.stage_name = 'SCHEDULER_FIRE')
                ORDER BY MIN(t.recorded_at) DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        log.warning(f"[scheduler_trace] get_latest_complete_trace failed: {e}")
        return None
