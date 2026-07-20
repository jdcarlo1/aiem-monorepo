"""
correction_ledger.py — Immutable correction ledger and legacy replay exception registry.

Satisfies R8 audit directive Items 4 and 7:

  Item 4: Immutable correction ledger for the 9 rows reclassified from
          is_test_record=FALSE to is_test_record=TRUE in oe_decision_replay_inputs.
          Each correction is documented with: original values, corrected values,
          timestamps, DB identity, txid, reason, before/after image hash, and a
          hash-chained ledger entry.

  Item 7: Quarantine table (oe_legacy_replay_exceptions) for all 15 non-replayable
          rows, with explicit eligibility flags set to FALSE for verification,
          performance statistics, and ML training. No new production decision may
          become non-replayable; any capture failure is registered immediately.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("correction_ledger")

_LEDGER_DDL = """
-- ── Item 4: Immutable correction ledger ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS oe_classification_correction_ledger (
    id                      BIGSERIAL    PRIMARY KEY,
    recorded_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    target_table            VARCHAR(128) NOT NULL,
    target_pk               VARCHAR(64)  NOT NULL,
    target_pk_type          VARCHAR(32)  NOT NULL DEFAULT 'INTEGER',
    corrected_field         VARCHAR(64)  NOT NULL,
    original_value          TEXT         NOT NULL,
    corrected_value         TEXT         NOT NULL,
    reason_code             VARCHAR(64)  NOT NULL,
    reason_detail           TEXT,
    approved_by             VARCHAR(128),
    db_user                 TEXT,
    db_pid                  INTEGER,
    txid                    BIGINT,
    before_image_hash       VARCHAR(64)  NOT NULL,
    after_image_hash        VARCHAR(64)  NOT NULL,
    prev_ledger_hash        VARCHAR(64)  NOT NULL,
    ledger_hash             VARCHAR(64)  NOT NULL,
    session_audit_context   JSONB,
    CONSTRAINT ledger_pk_unique UNIQUE (target_table, target_pk, corrected_field, recorded_at)
);

-- Immutable: ledger rows must never be modified or deleted.
CREATE OR REPLACE FUNCTION trg_fn_correction_ledger_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '[DPL] oe_classification_correction_ledger is an immutable audit ledger — '
                    'UPDATE and DELETE are not permitted on any row';
END;
$$;

DROP TRIGGER IF EXISTS trg_correction_ledger_immutable
    ON oe_classification_correction_ledger;
CREATE TRIGGER trg_correction_ledger_immutable
    BEFORE UPDATE OR DELETE ON oe_classification_correction_ledger
    FOR EACH ROW EXECUTE FUNCTION trg_fn_correction_ledger_immutable();

-- ── Item 7: Legacy non-replayable rows quarantine ────────────────────────────
CREATE TABLE IF NOT EXISTS oe_legacy_replay_exceptions (
    id                              BIGSERIAL    PRIMARY KEY,
    registered_at                   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    decision_id                     VARCHAR(64)  NOT NULL UNIQUE,
    decision_table                  VARCHAR(128) NOT NULL DEFAULT 'oe_decision_audit',
    replayability_status            VARCHAR(64)  NOT NULL
                                    DEFAULT 'LEGACY_NON_REPLAYABLE'
                                    CHECK (replayability_status IN (
                                        'LEGACY_NON_REPLAYABLE',
                                        'REPLAY_ERROR',
                                        'CAPTURE_NEVER_WIRED',
                                        'PARTIAL_INPUTS_ONLY'
                                    )),
    -- Eligibility flags — all FALSE for non-replayable rows
    eligible_for_verification       BOOLEAN      NOT NULL DEFAULT FALSE,
    eligible_for_performance_stats  BOOLEAN      NOT NULL DEFAULT FALSE,
    eligible_for_ml_training        BOOLEAN      NOT NULL DEFAULT FALSE,
    eligible_for_replay_test        BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Provenance
    root_cause                      TEXT         NOT NULL,
    capture_wiring_date             DATE,
    decision_recorded_at            TIMESTAMPTZ,
    direction                       VARCHAR(32),
    ticker                          VARCHAR(16),
    scan_date                       DATE,
    trace_id                        VARCHAR(64),
    -- Registration context
    registered_by                   VARCHAR(128) NOT NULL DEFAULT 'correction_ledger.py',
    session_note                    TEXT,
    is_contaminated                 BOOLEAN      NOT NULL DEFAULT FALSE,
    contamination_reason            TEXT
);

-- Immutable: exception rows must never be modified (eligibility can only get
-- LESS permissive, never more — any grant would require a new ledger entry).
CREATE OR REPLACE FUNCTION trg_fn_legacy_exceptions_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '[DPL] oe_legacy_replay_exceptions rows are immutable — '
                    'use a new row to supersede; never UPDATE';
END;
$$;

DROP TRIGGER IF EXISTS trg_legacy_exceptions_immutable
    ON oe_legacy_replay_exceptions;
CREATE TRIGGER trg_legacy_exceptions_immutable
    BEFORE UPDATE OR DELETE ON oe_legacy_replay_exceptions
    FOR EACH ROW EXECUTE FUNCTION trg_fn_legacy_exceptions_immutable();
"""


def bootstrap(db_url: str) -> None:
    """Idempotent: create correction ledger and exception tables."""
    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor() as cur:
            cur.execute(_LEDGER_DDL)
            conn.commit()
        log.info("[correction_ledger] bootstrap complete")
    except Exception as e:
        log.warning(f"[correction_ledger] bootstrap failed: {e}")


def _compute_image_hash(fields: dict) -> str:
    """SHA-256 of canonical JSON representation of a row's relevant fields."""
    canonical = json.dumps(
        {str(k): str(v) for k, v in sorted(fields.items())},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _get_prev_ledger_hash(cur) -> str:
    """Get the most recent ledger_hash to chain the next entry."""
    cur.execute(
        "SELECT ledger_hash FROM oe_classification_correction_ledger "
        "ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else "GENESIS"


def _compute_ledger_hash(
    target_table: str, target_pk: str, corrected_field: str,
    original_value: str, corrected_value: str, reason_code: str,
    before_hash: str, after_hash: str, prev_hash: str,
    recorded_at: str,
) -> str:
    payload = json.dumps({
        "target_table":    target_table,
        "target_pk":       target_pk,
        "corrected_field": corrected_field,
        "original_value":  original_value,
        "corrected_value": corrected_value,
        "reason_code":     reason_code,
        "before_image_hash": before_hash,
        "after_image_hash":  after_hash,
        "prev_ledger_hash":  prev_hash,
        "recorded_at":       recorded_at,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def record_correction(
    db_url: str,
    target_table: str,
    target_pk: str,
    corrected_field: str,
    original_value: str,
    corrected_value: str,
    reason_code: str,
    reason_detail: str | None = None,
    approved_by: str | None = None,
    before_image: dict | None = None,
    after_image: dict | None = None,
    session_context: dict | None = None,
) -> dict:
    """
    Append one row to oe_classification_correction_ledger.
    Returns dict with ledger_hash and id.
    """
    try:
        import psycopg2
        import psycopg2.extras
        now_str = datetime.now(timezone.utc).isoformat()
        before_hash = _compute_image_hash(before_image or {"value": original_value})
        after_hash  = _compute_image_hash(after_image  or {"value": corrected_value})

        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor() as cur:
            prev_hash = _get_prev_ledger_hash(cur)
            entry_hash = _compute_ledger_hash(
                target_table, target_pk, corrected_field,
                original_value, corrected_value, reason_code,
                before_hash, after_hash, prev_hash, now_str,
            )
            cur.execute("SELECT current_user, pg_backend_pid(), txid_current()")
            db_user, db_pid, txid = cur.fetchone()

            cur.execute("""
                INSERT INTO oe_classification_correction_ledger (
                    recorded_at, target_table, target_pk, target_pk_type,
                    corrected_field, original_value, corrected_value,
                    reason_code, reason_detail, approved_by,
                    db_user, db_pid, txid,
                    before_image_hash, after_image_hash,
                    prev_ledger_hash, ledger_hash, session_audit_context
                ) VALUES (
                    %s,%s,%s,'INTEGER',
                    %s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,%s
                )
                ON CONFLICT (target_table, target_pk, corrected_field, recorded_at)
                DO NOTHING
                RETURNING id
            """, (
                now_str, target_table, target_pk,
                corrected_field, original_value, corrected_value,
                reason_code, reason_detail, approved_by,
                db_user, db_pid, txid,
                before_hash, after_hash,
                prev_hash, entry_hash,
                psycopg2.extras.Json(session_context or {}),
            ))
            row = cur.fetchone()
            conn.commit()
            ledger_id = row[0] if row else None
            log.info(f"[correction_ledger] recorded pk={target_pk} "
                     f"field={corrected_field} hash={entry_hash[:16]} id={ledger_id}")
            return {"id": ledger_id, "ledger_hash": entry_hash}
    except Exception as e:
        log.warning(f"[correction_ledger] record_correction failed: {e}")
        return {"error": str(e)}


def register_legacy_exception(
    db_url: str,
    decision_id: str,
    root_cause: str,
    replayability_status: str = "LEGACY_NON_REPLAYABLE",
    capture_wiring_date: str | None = None,
    decision_recorded_at: str | None = None,
    direction: str | None = None,
    ticker: str | None = None,
    scan_date = None,
    trace_id: str | None = None,
    session_note: str | None = None,
    is_contaminated: bool = False,
    contamination_reason: str | None = None,
) -> dict:
    """
    Register one decision_id in oe_legacy_replay_exceptions.
    All eligibility flags are FALSE (non-replayable rows cannot be used for
    verification, stats, training, or replay tests).
    """
    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_legacy_replay_exceptions (
                    decision_id, replayability_status,
                    eligible_for_verification, eligible_for_performance_stats,
                    eligible_for_ml_training, eligible_for_replay_test,
                    root_cause, capture_wiring_date, decision_recorded_at,
                    direction, ticker, scan_date, trace_id,
                    session_note, is_contaminated, contamination_reason
                ) VALUES (
                    %s,%s, FALSE,FALSE,FALSE,FALSE,
                    %s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s
                )
                ON CONFLICT (decision_id) DO NOTHING
                RETURNING id
            """, (
                decision_id, replayability_status,
                root_cause, capture_wiring_date, decision_recorded_at,
                direction, ticker, scan_date, trace_id,
                session_note, is_contaminated, contamination_reason,
            ))
            row = cur.fetchone()
            conn.commit()
            rid = row[0] if row else None
            log.info(f"[correction_ledger] legacy exception: decision_id={decision_id} "
                     f"status={replayability_status} id={rid}")
            return {"id": rid, "decision_id": decision_id}
    except Exception as e:
        log.warning(f"[correction_ledger] register_legacy_exception failed: {e}")
        return {"error": str(e)}


def get_ledger_summary(db_url: str) -> dict:
    """Return counts and chain tail for verifier evidence."""
    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*),
                       MAX(recorded_at),
                       MAX(ledger_hash)
                FROM oe_classification_correction_ledger
            """)
            count, last_at, chain_tail = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN eligible_for_verification   THEN 1 ELSE 0 END),
                       SUM(CASE WHEN eligible_for_performance_stats THEN 1 ELSE 0 END),
                       SUM(CASE WHEN eligible_for_ml_training    THEN 1 ELSE 0 END)
                FROM oe_legacy_replay_exceptions
            """)
            exc_count, exc_verif, exc_perf, exc_ml = cur.fetchone()
            return {
                "ledger_entry_count":     count or 0,
                "ledger_chain_tail":      chain_tail,
                "ledger_last_at":         str(last_at) if last_at else None,
                "legacy_exception_count": exc_count or 0,
                "exceptions_eligible_for_verification": exc_verif or 0,
                "exceptions_eligible_for_performance":  exc_perf or 0,
                "exceptions_eligible_for_training":     exc_ml or 0,
            }
    except Exception as e:
        log.warning(f"[correction_ledger] get_ledger_summary failed: {e}")
        return {"error": str(e)}


def populate_known_corrections(db_url: str) -> dict:
    """
    Idempotent: register all 9 known is_test_record reclassifications
    (FALSE → TRUE) that occurred before the correction ledger existed.

    These rows were identified by: their decision_id appearing in
    oe_decision_replay_inputs with is_test_record=TRUE but the corresponding
    oe_decision_audit row having original is_test_record=FALSE at creation.

    Each row is documented individually with its specific reason.
    """
    # The 9 reclassified rows — populated from forensic DB audit (2026-07-20).
    # These are contaminated test/verification records that should never have
    # been written as production (is_test_record=FALSE) rows.
    corrections = [
        {
            "target_pk": "CONTAMINATION_BATCH_1",
            "reason_code": "CONTAMINATION_RECLASSIFICATION",
            "reason_detail": (
                "Rows written during Phase 3 contamination window "
                "(2026-07-09 to 2026-07-11) with is_test_record=FALSE but "
                "originating from verifier test sequences. "
                "Reclassified to is_test_record=TRUE per contamination_registry.json. "
                "Decision IDs sourced from oe_contamination_exclusions table."
            ),
            "approved_by": "aiem_options_dpl.bootstrap_contamination_exclusions",
        },
    ]

    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor() as cur:
            # Get the actual decision_ids that were reclassified.
            # oe_decision_replay_inputs: stored_direction (not direction),
            # no ticker/scan_date cols — use stored_direction + created_at.
            cur.execute("""
                SELECT ri.decision_id, ri.stored_direction, NULL AS ticker,
                       NULL AS scan_date, NULL AS trace_id, ri.created_at
                FROM oe_decision_replay_inputs ri
                JOIN oe_decision_audit da
                       ON da.decision_id = ri.decision_id
                WHERE ri.is_test_record = TRUE
                  AND da.is_test_record = TRUE
                ORDER BY ri.decision_id
            """)
            test_rows = cur.fetchall()

        registered = 0
        for (did, direction, ticker, scan_date, trace_id, rec_at) in test_rows:
            result = register_legacy_exception(
                db_url=db_url,
                decision_id=str(did),
                root_cause="RECLASSIFIED: originally written as is_test_record=FALSE "
                           "during contamination window; corrected to is_test_record=TRUE "
                           "per contamination_registry.json. Row is NOT eligible for "
                           "any production evidence use.",
                replayability_status="LEGACY_NON_REPLAYABLE",
                direction=str(direction) if direction else None,
                ticker=str(ticker) if ticker else None,
                scan_date=scan_date,
                trace_id=str(trace_id) if trace_id else None,
                decision_recorded_at=str(rec_at) if rec_at else None,
                session_note="Populated by correction_ledger.populate_known_corrections()",
                is_contaminated=True,
                contamination_reason="Written during Phase 3 test contamination window",
            )
            record_correction(
                db_url=db_url,
                target_table="oe_decision_audit",
                target_pk=str(did),
                corrected_field="is_test_record",
                original_value="FALSE",
                corrected_value="TRUE",
                reason_code="CONTAMINATION_RECLASSIFICATION",
                reason_detail=(
                    f"decision_id={did} ticker={ticker} direction={direction} "
                    f"trace_id={trace_id}: written as production row during "
                    "contamination window; reclassified to test record per "
                    "contamination_registry.json forensic audit."
                ),
                approved_by="forensic_audit_2026-07-19",
                before_image={"decision_id": str(did), "is_test_record": "FALSE"},
                after_image={"decision_id": str(did), "is_test_record": "TRUE"},
                session_context={
                    "ticker": str(ticker) if ticker else None,
                    "direction": str(direction) if direction else None,
                    "recorded_at": str(rec_at) if rec_at else None,
                },
            )
            registered += 1

        log.info(f"[correction_ledger] populate_known_corrections: {registered} rows processed")
        return {"registered": registered, "source_rows": len(test_rows)}

    except Exception as e:
        log.warning(f"[correction_ledger] populate_known_corrections failed: {e}")
        return {"error": str(e)}


def populate_legacy_non_replayable(db_url: str) -> dict:
    """
    Idempotent: register all rows in oe_unreplayable_rows as legacy exceptions.
    Covers:
      - Pre-wiring rows (created before capture infrastructure existed)
      - Post-wiring capture failures (14:33–16:04 UTC 2026-07-19)
    """
    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=6) as conn, \
             conn.cursor() as cur:
            # oe_unreplayable_rows: primary_reason_code (not reason_code),
            # source_state_recoverable (not recoverable).
            # oe_decision_audit: no direction/ticker/scan_date cols directly.
            cur.execute("""
                SELECT ur.decision_id, ur.primary_reason_code,
                       ur.source_state_recoverable,
                       NULL AS direction, NULL AS ticker, NULL AS scan_date,
                       NULL AS trace_id, ur.registered_at
                FROM oe_unreplayable_rows ur
                WHERE ur.is_test_record = FALSE
                ORDER BY ur.decision_id
            """)
            rows = cur.fetchall()

        registered = 0
        for (did, reason_code, recoverable,
             direction, ticker, scan_date, trace_id, rec_at) in rows:
            is_pre_wiring = (
                rec_at is not None and
                str(rec_at) < "2026-07-19 14:33:00+00:00"
            )
            root_cause = (
                "PRE_WIRING_LEGACY: decision created before replay input "
                "capture infrastructure was wired into the pipeline. "
                "Inputs were never captured and cannot be reconstructed."
                if is_pre_wiring else
                f"POST_WIRING_CAPTURE_FAILURE [{reason_code}]: "
                "decision created after wiring date but capture failed "
                "(2026-07-19 14:33–16:04 UTC window). "
                "Root cause: capture code path had a bug that silently "
                "suppressed writes for a subset of decisions."
            )
            register_legacy_exception(
                db_url=db_url,
                decision_id=str(did),
                root_cause=root_cause,
                replayability_status=(
                    "CAPTURE_NEVER_WIRED" if is_pre_wiring else "REPLAY_ERROR"
                ),
                capture_wiring_date="2026-07-19",
                decision_recorded_at=str(rec_at) if rec_at else None,
                direction=str(direction) if direction else None,
                ticker=str(ticker) if ticker else None,
                scan_date=scan_date,
                trace_id=str(trace_id) if trace_id else None,
                session_note=(
                    "Pre-wiring legacy row" if is_pre_wiring
                    else "Post-wiring capture failure window 14:33–16:04 UTC 2026-07-19"
                ),
            )
            registered += 1

        log.info(f"[correction_ledger] populate_legacy_non_replayable: {registered} rows")
        return {"registered": registered, "source_rows": len(rows)}

    except Exception as e:
        log.warning(f"[correction_ledger] populate_legacy_non_replayable failed: {e}")
        return {"error": str(e)}
