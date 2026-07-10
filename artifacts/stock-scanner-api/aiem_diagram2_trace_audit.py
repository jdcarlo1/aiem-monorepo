"""
aiem_diagram2_trace_audit.py
-----------------------------
Runtime trace audit for the 21-stage Diagram 2 pipeline, per the user's
"FINAL DIAGRAM 2 REMEDIATION" authorization. Every real candidate that
flows through AEIMMasterOrchestrator.execute_stage() gets one row per
stage here, with input/output hashes AND the full serialized payload_json,
so the full trace can be independently verified with a single SQL query per
trace_id — without joining to any other table.

REMEDIATION FIX (A1, Jul 2026): added `payload_json JSONB` column alongside
the existing hash columns. `record_stage()` now writes the actual input+output
dict to payload_json. Hashes are preserved for consistency proofs; payload_json
provides raw inspectability for every conviction-layer feature value.

REMEDIATION FIX (S1, "AUTHORITATIVE MASTER REMEDIATION" directive Phase 2/
Phase 9 step 2, Jul 2026): added the standardized trace/provenance contract
columns (root_trace_id, candidate_id, provenance_parent_hash, provenance_hash,
reason_codes) plus the explicit-terminal-rejection columns required by P0-4
(terminal_status, rejected_at_stage_order, rejected_at_stage_name,
rejecting_component, human_readable_reason, last_successful_stage,
no_order_created). Purely additive — no existing column, constraint, or
call site is removed or renamed. `record_stage()` now maintains a real
hash chain (provenance_hash = sha256(prev_provenance_hash + this stage's
input/output hashes)) so tamper/reordering is independently detectable per
trace_id with one SQL query. New helper `record_terminal()` writes the P0-4
terminal REJECTED/BLOCKED row using a reserved stage_order sentinel (99) so
the existing UNIQUE(trace_id, stage_order) constraint is preserved without
migration.

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
    payload_json      JSONB,
    evidence_pointer  TEXT,
    error_message     TEXT,
    UNIQUE (trace_id, stage_order)
);
CREATE INDEX IF NOT EXISTS aiem_d2_trace_idx  ON aiem_diagram2_trace_audit(trace_id);
CREATE INDEX IF NOT EXISTS aiem_d2_ticker_idx ON aiem_diagram2_trace_audit(ticker, started_at DESC);
"""

_MIGRATION_DDL = """
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS payload_json JSONB;
"""

# REMEDIATION S1 (trace/provenance contract + P0-4 terminal rejection audit).
# Purely additive: every column is nullable, so every pre-existing row and
# every pre-existing INSERT/UPDATE statement remains valid untouched.
# stage_order=99 is a reserved sentinel for terminal rows (never used by any
# of the real 21 pipeline stages), so the existing
# UNIQUE(trace_id, stage_order) constraint needs no change to support one
# terminal row per trace_id.
_S1_TRACE_CONTRACT_DDL = """
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS root_trace_id TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS candidate_id TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS provenance_parent_hash TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS provenance_hash TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS reason_codes JSONB;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS terminal_status TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS rejected_at_stage_order INT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS rejected_at_stage_name TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS rejecting_component TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS human_readable_reason TEXT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS last_successful_stage INT;
ALTER TABLE aiem_diagram2_trace_audit ADD COLUMN IF NOT EXISTS no_order_created BOOLEAN;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'aiem_d2_terminal_status_chk'
    ) THEN
        ALTER TABLE aiem_diagram2_trace_audit
            ADD CONSTRAINT aiem_d2_terminal_status_chk
            CHECK (terminal_status IS NULL OR terminal_status IN ('REJECTED', 'BLOCKED'));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS aiem_d2_root_trace_idx ON aiem_diagram2_trace_audit(root_trace_id);
CREATE INDEX IF NOT EXISTS aiem_d2_terminal_idx ON aiem_diagram2_trace_audit(terminal_status) WHERE terminal_status IS NOT NULL;
"""

TERMINAL_STAGE_ORDER = 99


def init_schema(db_url: str = None) -> None:
    try:
        _db = db_url or _db_url()
        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _cu.execute(DDL)
            _cu.execute(_MIGRATION_DDL)
            _cu.execute(_S1_TRACE_CONTRACT_DDL)
            _c.commit()
        print("[aiem_diagram2_trace_audit] schema ready (payload_json + S1 trace/provenance/terminal columns ensured)")
    except Exception as _e:
        print(f"[aiem_diagram2_trace_audit] schema init error: {_e}")


def _hash(obj) -> str:
    """Stable sha256 hash (truncated) of any JSON-serializable payload."""
    try:
        blob = json.dumps(obj, sort_keys=True, default=str)
    except Exception:
        blob = str(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _prior_provenance_hash(_cu, trace_id: str, before_stage_order: int):
    """
    Look up the provenance_hash of the most recently recorded stage for this
    trace_id, strictly before before_stage_order. Returns None for the first
    stage in a trace (correct: a hash chain's genesis link has no parent).
    """
    _cu.execute("""
        SELECT provenance_hash FROM aiem_diagram2_trace_audit
        WHERE trace_id = %s AND stage_order < %s AND provenance_hash IS NOT NULL
        ORDER BY stage_order DESC LIMIT 1
    """, (trace_id, before_stage_order))
    _row = _cu.fetchone()
    return _row[0] if _row else None


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
    root_trace_id: str = None,
    candidate_id: str = None,
    reason_codes=None,
) -> bool:
    """
    Write one Diagram 2 stage row. Stores both the hash (for consistency proofs)
    and the full serialized payload_json (for raw inspectability).
    Non-fatal: returns False (never raises) on any DB error so a broken audit
    write can NEVER block a live trade.

    S1 trace/provenance contract: also chains provenance_hash to the previous
    stage's provenance_hash for the same trace_id (provenance_parent_hash),
    so the full stage sequence for a trace_id is tamper/reorder-evident with
    a single SQL query, independent of stage_order alone. root_trace_id
    defaults to trace_id itself (a candidate's own trace is its own root
    unless a caller explicitly links it to a wider batch/run root).
    """
    try:
        _db = db_url or _db_url()

        # Build combined payload: {"input": ..., "output": ...}
        _combined: dict = {}
        if input_payload is not None:
            _combined["input"] = input_payload
        if output_payload is not None:
            _combined["output"] = output_payload
        try:
            _payload_json_str = json.dumps(_combined, default=str) if _combined else None
        except Exception:
            _payload_json_str = json.dumps({"_serialize_error": str(_combined)[:500]})

        _in_hash  = _hash(input_payload) if input_payload is not None else None
        _out_hash = _hash(output_payload) if output_payload is not None else None
        _reason_codes_json = json.dumps(reason_codes, default=str) if reason_codes is not None else None
        _root = root_trace_id or trace_id

        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _parent_hash = _prior_provenance_hash(_cu, trace_id, stage_order)
            _prov_hash = _hash({
                "parent": _parent_hash, "trace_id": trace_id, "stage_order": stage_order,
                "input_hash": _in_hash, "output_hash": _out_hash, "status": status,
            })
            _cu.execute("""
                INSERT INTO aiem_diagram2_trace_audit
                    (trace_id, ticker, paper_trade_id, stage_order, stage_name,
                     component_name, runtime_function, status, started_at,
                     completed_at, input_hash, output_hash, payload_json,
                     evidence_pointer, error_message, root_trace_id, candidate_id,
                     provenance_parent_hash, provenance_hash, reason_codes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s::jsonb,%s,%s,
                        %s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (trace_id, stage_order) DO UPDATE SET
                    status=EXCLUDED.status, completed_at=NOW(),
                    output_hash=EXCLUDED.output_hash,
                    payload_json=EXCLUDED.payload_json,
                    evidence_pointer=EXCLUDED.evidence_pointer,
                    error_message=EXCLUDED.error_message,
                    provenance_parent_hash=EXCLUDED.provenance_parent_hash,
                    provenance_hash=EXCLUDED.provenance_hash,
                    reason_codes=EXCLUDED.reason_codes
            """, (
                trace_id, str(ticker).upper().strip(), paper_trade_id, stage_order,
                stage_name, component_name, runtime_function, status, started_at,
                _in_hash, _out_hash,
                _payload_json_str,
                evidence_pointer, error_message,
                _root, candidate_id, _parent_hash, _prov_hash, _reason_codes_json,
            ))
            _c.commit()
        return True
    except Exception as _e:
        print(f"[aiem_diagram2_trace_audit] record_stage error ({stage_name}): {_e}")
        return False


def record_terminal(
    trace_id: str,
    ticker: str,
    terminal_status: str,
    rejected_at_stage_order: int,
    rejected_at_stage_name: str,
    rejecting_component: str,
    human_readable_reason: str,
    last_successful_stage: int = None,
    reason_codes=None,
    candidate_id: str = None,
    root_trace_id: str = None,
    paper_trade_id: int = None,
    started_at: dt.datetime = None,
    db_url: str = None,
) -> bool:
    """
    P0-4: write the single explicit terminal row for a candidate that never
    reached order execution — REJECTED (a gate legitimately said no) or
    BLOCKED (governance/safety stopped it). Every candidate trace_id must end
    in either 17 real PASS stages -> an order, OR exactly one of these
    terminal rows. no_order_created is always True here by construction —
    this function must never be called for a trace_id that already produced
    a paper trade.

    Uses the reserved TERMINAL_STAGE_ORDER=99 sentinel so the existing
    UNIQUE(trace_id, stage_order) constraint gives exactly one terminal row
    per trace_id for free (ON CONFLICT DO UPDATE keeps the latest reason if
    called twice for the same trace_id, which should not happen in practice).

    Non-fatal: returns False (never raises) on any DB error, matching
    record_stage()'s contract — an audit-write failure must never itself
    block or crash the candidate-rejection path that called it.
    """
    if terminal_status not in ("REJECTED", "BLOCKED"):
        print(f"[aiem_diagram2_trace_audit] record_terminal invalid terminal_status={terminal_status!r} (must be REJECTED or BLOCKED)")
        return False
    try:
        _db = db_url or _db_url()
        _started = started_at or dt.datetime.now(dt.timezone.utc)
        _reason_codes_json = json.dumps(reason_codes, default=str) if reason_codes is not None else None
        _root = root_trace_id or trace_id
        _combined = {
            "terminal_status": terminal_status,
            "rejected_at_stage_order": rejected_at_stage_order,
            "rejected_at_stage_name": rejected_at_stage_name,
            "rejecting_component": rejecting_component,
            "human_readable_reason": human_readable_reason,
            "reason_codes": reason_codes,
        }
        _payload_json_str = json.dumps(_combined, default=str)

        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _parent_hash = _prior_provenance_hash(_cu, trace_id, TERMINAL_STAGE_ORDER)
            _prov_hash = _hash({
                "parent": _parent_hash, "trace_id": trace_id,
                "stage_order": TERMINAL_STAGE_ORDER, "terminal_status": terminal_status,
                "rejected_at_stage_order": rejected_at_stage_order,
            })
            _cu.execute("""
                INSERT INTO aiem_diagram2_trace_audit
                    (trace_id, ticker, paper_trade_id, stage_order, stage_name,
                     component_name, runtime_function, status, started_at,
                     completed_at, payload_json, error_message,
                     root_trace_id, candidate_id, provenance_parent_hash,
                     provenance_hash, reason_codes, terminal_status,
                     rejected_at_stage_order, rejected_at_stage_name,
                     rejecting_component, human_readable_reason,
                     last_successful_stage, no_order_created)
                VALUES (%s,%s,%s,%s,'TERMINAL',%s,%s,'FAIL',%s,NOW(),%s::jsonb,%s,
                        %s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,TRUE)
                ON CONFLICT (trace_id, stage_order) DO UPDATE SET
                    completed_at=NOW(),
                    payload_json=EXCLUDED.payload_json,
                    error_message=EXCLUDED.error_message,
                    provenance_parent_hash=EXCLUDED.provenance_parent_hash,
                    provenance_hash=EXCLUDED.provenance_hash,
                    reason_codes=EXCLUDED.reason_codes,
                    terminal_status=EXCLUDED.terminal_status,
                    rejected_at_stage_order=EXCLUDED.rejected_at_stage_order,
                    rejected_at_stage_name=EXCLUDED.rejected_at_stage_name,
                    rejecting_component=EXCLUDED.rejecting_component,
                    human_readable_reason=EXCLUDED.human_readable_reason,
                    last_successful_stage=EXCLUDED.last_successful_stage,
                    no_order_created=TRUE
            """, (
                trace_id, str(ticker).upper().strip(), paper_trade_id, TERMINAL_STAGE_ORDER,
                rejecting_component, runtime_function_placeholder(), _started,
                _payload_json_str, human_readable_reason,
                _root, candidate_id, _parent_hash, _prov_hash, _reason_codes_json,
                terminal_status, rejected_at_stage_order, rejected_at_stage_name,
                rejecting_component, human_readable_reason, last_successful_stage,
            ))
            _c.commit()
        return True
    except Exception as _e:
        print(f"[aiem_diagram2_trace_audit] record_terminal error ({terminal_status}@{rejected_at_stage_name}): {_e}")
        return False


def runtime_function_placeholder() -> str:
    """runtime_function is NOT NULL on the table; terminal rows describe a
    rejection decision rather than a single function call, so this fixed
    label keeps the NOT NULL contract honest without inventing a fake
    function name."""
    return "record_terminal (terminal rejection/block — no single stage function)"


def summary(trace_id: str, db_url: str = None) -> dict:
    """Hard SQL proof query, wrapped for convenience."""
    try:
        _db = db_url or _db_url()
        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _cu.execute("""
                SELECT trace_id, ticker, COUNT(*) AS stages_recorded,
                       MIN(stage_order) AS first_stage, MAX(stage_order) AS last_stage,
                       SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END) AS pass_count,
                       SUM(CASE WHEN status!='PASS' THEN 1 ELSE 0 END) AS fail_count,
                       SUM(CASE WHEN payload_json IS NOT NULL THEN 1 ELSE 0 END) AS with_payload,
                       MAX(terminal_status) AS terminal_status,
                       BOOL_OR(no_order_created) AS no_order_created
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
            "stages_with_payload": row[7],
            "terminal_status": row[8],
            "no_order_created": row[9],
        }
    except Exception as _e:
        return {"error": str(_e)}


def ordered_stages(trace_id: str, db_url: str = None) -> list:
    try:
        _db = db_url or _db_url()
        with psycopg2.connect(_db, connect_timeout=4) as _c, _c.cursor() as _cu:
            _cu.execute("""
                SELECT stage_order, stage_name, component_name, runtime_function,
                       status, evidence_pointer, error_message, started_at, completed_at,
                       payload_json, root_trace_id, candidate_id, provenance_parent_hash,
                       provenance_hash, reason_codes, terminal_status,
                       rejected_at_stage_order, rejected_at_stage_name,
                       rejecting_component, human_readable_reason,
                       last_successful_stage, no_order_created
                FROM aiem_diagram2_trace_audit
                WHERE trace_id = %s
                ORDER BY stage_order
            """, (trace_id,))
            rows = _cu.fetchall()
        return [
            {"stage_order": r[0], "stage_name": r[1], "component_name": r[2],
             "runtime_function": r[3], "status": r[4], "evidence_pointer": r[5],
             "error_message": r[6], "started_at": str(r[7]), "completed_at": str(r[8]),
             "payload_json": r[9], "root_trace_id": r[10], "candidate_id": r[11],
             "provenance_parent_hash": r[12], "provenance_hash": r[13],
             "reason_codes": r[14], "terminal_status": r[15],
             "rejected_at_stage_order": r[16], "rejected_at_stage_name": r[17],
             "rejecting_component": r[18], "human_readable_reason": r[19],
             "last_successful_stage": r[20], "no_order_created": r[21]}
            for r in rows
        ]
    except Exception as _e:
        return [{"error": str(_e)}]
