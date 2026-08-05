"""
Durable stage/module diagnostics for AEIM orchestrator _h_* handlers.

Writes one row per completed handler to:
  - aiem_diagnostics  (stage-oriented; payload JSON)
  - aiem_pipeline      (module-oriented; same payload pointer)

packet.audit remains in-memory; these tables are the durable flush.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_DDL = """
CREATE TABLE IF NOT EXISTS aiem_diagnostics (
    id           BIGSERIAL PRIMARY KEY,
    trace_id     TEXT        NOT NULL,
    ticker       TEXT,
    stage_name   TEXT        NOT NULL,
    module_name  TEXT        NOT NULL,
    status       TEXT        NOT NULL,
    payload      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS aiem_diagnostics_trace_idx
    ON aiem_diagnostics(trace_id);
CREATE INDEX IF NOT EXISTS aiem_diagnostics_stage_idx
    ON aiem_diagnostics(stage_name, created_at DESC);

CREATE TABLE IF NOT EXISTS aiem_pipeline (
    id           BIGSERIAL PRIMARY KEY,
    trace_id     TEXT        NOT NULL,
    ticker       TEXT,
    module_name  TEXT        NOT NULL,
    stage_name   TEXT,
    status       TEXT        NOT NULL,
    payload      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS aiem_pipeline_trace_idx
    ON aiem_pipeline(trace_id);
CREATE INDEX IF NOT EXISTS aiem_pipeline_module_idx
    ON aiem_pipeline(module_name, created_at DESC);
"""

_SCHEMA_READY = False


def ensure_schema(db_url: Optional[str] = None) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    import psycopg2
    url = db_url or os.environ.get("DATABASE_URL") or os.environ.get("AIEM_DATABASE_URL") or ""
    if not url:
        raise RuntimeError("DATABASE_URL required for aiem_stage_diagnostics")
    with psycopg2.connect(url, connect_timeout=8) as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()
    _SCHEMA_READY = True


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            pass
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return repr(obj)[:2000]


def persist_stage(
    *,
    trace_id: str,
    ticker: str,
    stage_name: str,
    module_name: str,
    status: str,
    payload: Optional[Dict[str, Any]] = None,
    db_url: Optional[str] = None,
) -> None:
    """INSERT into aiem_diagnostics AND aiem_pipeline (fail-soft on error)."""
    try:
        ensure_schema(db_url)
        import psycopg2
        import psycopg2.extras
        url = db_url or os.environ.get("DATABASE_URL") or os.environ.get("AIEM_DATABASE_URL") or ""
        status_n = (status or "FAIL").upper()
        if status_n in ("SUCCESS", "OK", "PASS"):
            status_n = "PASS"
        elif status_n in ("FAILED", "ERROR", "FAIL"):
            status_n = "FAIL"
        elif status_n not in ("PASS", "FAIL", "PARTIAL", "SKIP"):
            status_n = "FAIL" if "error" in status_n.lower() else "PASS"
        body = _jsonable(payload or {})
        with psycopg2.connect(url, connect_timeout=8) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aiem_diagnostics
                    (trace_id, ticker, stage_name, module_name, status, payload, created_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    trace_id, ticker, stage_name, module_name, status_n,
                    json.dumps(body), datetime.now(timezone.utc),
                ),
            )
            cur.execute(
                """
                INSERT INTO aiem_pipeline
                    (trace_id, ticker, module_name, stage_name, status, payload, created_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    trace_id, ticker, module_name, stage_name, status_n,
                    json.dumps(body), datetime.now(timezone.utc),
                ),
            )
            conn.commit()
    except Exception as exc:
        print(f"[aiem_stage_diagnostics] persist_stage error ({stage_name}): {exc}")
