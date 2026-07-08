"""
aiem_diagram2_trace_audit.py
-----------------------------
Runtime trace audit for the 21-stage Diagram 2 pipeline, per the user's
"FINAL DIAGRAM 2 REMEDIATION" authorization. Every real candidate that
flows through AEIMMasterOrchestrator.execute_stage() gets one row per
stage here, with input/output hashes and an evidence pointer, so the
full trace can be independently verified with a single SQL query per
trace_id.

This module does NOT decide trading behavior. It only records what
happened, when, and where. Non-fatal by design: an audit-write failure
must never break a live trade.

Uses DATABASE_URL (the production DB) -- same as aiem_pipeline_audit.py
and aiem_paper_trades -- NOT AIEM_DATABASE_URL (that one is reserved for
research-isolated tables per the comment at main.py ~L30215).
"""

import os
import json
import hashlib
import datetime as dt

import psycopg2


def _db_url():
    return os.environ.get("DATABASE_URL", "")


DDL = """
CREATE TABLE IF NOT EXISTS aiem_diagram2_trace_audit (
    id                BIGSERIAL PRIMARY KEY,
    trace_id          TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    paper_trade_id    BIGINT,
    stage_order       INT NOT NULL,
    stage_name        TEXT NOT NULL,
    component_name    TEXT NOT NULL,
    runtime_function  TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('PASS','FAIL')),
    started_at        TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    input_hash        TEXT,
    output_hash       TEXT,
    evidence_pointer  TEXT,
    error_message     TEXT,
    UNIQUE (trace_id, stage_order)
);
CREATE INDEX IF NOT EXISTS aiem_d2_trace_idx  ON aiem_diagram2_trace_audit(trace_id);
CREATE INDEX IF NOT EXISTS aiem_d2_ticker_idx ON aiem_diagram2_trace_audit(ticker, started_at DESC);
"""


def init_schema(db_url: str = None) -> None:
    try:
        _db = db_url or _db_url()
        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _cu.execute(DDL)
            _c.commit()
        print("[aiem_diagram2_trace_audit] schema ready")
    except Exception as _e:
        print(f"[aiem_diagram2_trace_audit] schema init error: {_e}")


def _hash(obj) -> str:
    """Stable sha256 hash (truncated) of any JSON-serializable payload."""
    try:
        blob = json.dumps(obj, sort_keys=True, default=str)
    except Exception:
        blob = str(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def record_stage(
    trace_id: str,
    ticker: str,
    stage_order: int,
    stage_name: str,
    component_name: str,
    runtime_function: str,
    status: str,
    started_at: dt.datetime,
    input_payload=None,
    output_payload=None,
    evidence_pointer: str = None,
    error_message: str = None,
    paper_trade_id: int = None,
    db_url: str = None,
) -> bool:
    """
    Write one Diagram 2 stage row. Non-fatal: returns False (never raises)
    on any DB error so a broken audit write can NEVER block a live trade.
    """
    try:
        _db = db_url or _db_url()
        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _cu.execute("""
                INSERT INTO aiem_diagram2_trace_audit
                    (trace_id, ticker, paper_trade_id, stage_order, stage_name,
                     component_name, runtime_function, status, started_at,
                     completed_at, input_hash, output_hash, evidence_pointer,
                     error_message)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s)
                ON CONFLICT (trace_id, stage_order) DO UPDATE SET
                    status=EXCLUDED.status, completed_at=NOW(),
                    output_hash=EXCLUDED.output_hash,
                    evidence_pointer=EXCLUDED.evidence_pointer,
                    error_message=EXCLUDED.error_message
            """, (
                trace_id, str(ticker).upper().strip(), paper_trade_id, stage_order,
                stage_name, component_name, runtime_function, status, started_at,
                _hash(input_payload) if input_payload is not None else None,
                _hash(output_payload) if output_payload is not None else None,
                evidence_pointer, error_message,
            ))
            _c.commit()
        return True
    except Exception as _e:
        print(f"[aiem_diagram2_trace_audit] record_stage error ({stage_name}): {_e}")
        return False


def summary(trace_id: str, db_url: str = None) -> dict:
    """Hard SQL proof query, wrapped for convenience."""
    try:
        _db = db_url or _db_url()
        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _cu.execute("""
                SELECT trace_id, ticker, COUNT(*) AS stages_recorded,
                       MIN(stage_order) AS first_stage, MAX(stage_order) AS last_stage,
                       SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END) AS pass_count,
                       SUM(CASE WHEN status!='PASS' THEN 1 ELSE 0 END) AS fail_count
                FROM aiem_diagram2_trace_audit
                WHERE trace_id = %s
                GROUP BY trace_id, ticker
            """, (trace_id,))
            row = _cu.fetchone()
        if not row:
            return {"error": f"no rows for trace_id={trace_id}"}
        return {
            "trace_id": row[0], "ticker": row[1], "stages_recorded": row[2],
            "first_stage": row[3], "last_stage": row[4],
            "pass_count": row[5], "fail_count": row[6],
        }
    except Exception as _e:
        return {"error": str(_e)}


def ordered_stages(trace_id: str, db_url: str = None) -> list:
    try:
        _db = db_url or _db_url()
        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _cu.execute("""
                SELECT stage_order, stage_name, component_name, runtime_function,
                       status, evidence_pointer, error_message, started_at, completed_at
                FROM aiem_diagram2_trace_audit
                WHERE trace_id = %s
                ORDER BY stage_order
            """, (trace_id,))
            rows = _cu.fetchall()
        return [
            {"stage_order": r[0], "stage_name": r[1], "component_name": r[2],
             "runtime_function": r[3], "status": r[4], "evidence_pointer": r[5],
             "error_message": r[6], "started_at": str(r[7]), "completed_at": str(r[8])}
            for r in rows
        ]
    except Exception as _e:
        return [{"error": str(_e)}]
