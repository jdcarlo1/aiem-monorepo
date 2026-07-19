"""
aiem_options_dpl.py — Decision Proof Layer (DPL) Phase 1: Immutable Audit Record

Scope isolation: oe_decision_audit only. No D1/D2/D3 tables touched.
No execution-quality fields (fill probability, slippage, commission) — paper-mode only.
No decision-content capture in Phase 1 (Phase 2/3 scope).
"""

import hashlib
import json
import os
import uuid

import psycopg2

_DB_URL = os.environ.get("DATABASE_URL", "")
_DPL_TABLE = "oe_decision_audit"

_ENGINE_VERSION_FALLBACK = "no_active_champion"
_DB_VERSION_FALLBACK     = "unknown"


def _conn(db_url=None):
    url = db_url or _DB_URL
    return psycopg2.connect(url, connect_timeout=8,
                            options="-c statement_timeout=15000")


def _sha256(data: dict) -> str:
    """Deterministic SHA-256 of JSON (keys sorted for stability)."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _live_engine_version(cur) -> str:
    """Read active champion version_id from oe_model_versions (live source, not hardcoded)."""
    cur.execute(
        "SELECT version_id FROM oe_model_versions "
        "WHERE is_active = TRUE AND is_test_record = FALSE "
        "LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else _ENGINE_VERSION_FALLBACK


def _live_db_version(cur) -> str:
    """Read PostgreSQL version from live server (not hardcoded)."""
    cur.execute("SELECT split_part(version(), ' ', 2)")
    row = cur.fetchone()
    return row[0] if row else _DB_VERSION_FALLBACK


def _post_write_integrity_check(cur, decision_id: str,
                                 expected_input_hash: str,
                                 expected_output_hash: str) -> bool:
    """
    Reject-on-integrity-failure gate: re-read stored hashes immediately after
    INSERT and compare against expected values. Raises ValueError on mismatch.
    Returns True when verified.
    """
    cur.execute(
        "SELECT input_hash, output_hash FROM oe_decision_audit "
        "WHERE decision_id = %s",
        (decision_id,)
    )
    stored = cur.fetchone()
    if stored is None:
        raise ValueError(
            f"DPL integrity gate: row absent after INSERT "
            f"(decision_id={decision_id})"
        )
    stored_input, stored_output = stored
    if stored_input != expected_input_hash or stored_output != expected_output_hash:
        raise ValueError(
            f"DPL integrity gate: hash mismatch after INSERT — "
            f"input_match={stored_input == expected_input_hash} "
            f"output_match={stored_output == expected_output_hash}"
        )
    return True


def bootstrap_dpl(db_url=None) -> bool:
    """
    Create oe_decision_audit table and immutability trigger. Idempotent.
    Returns True on success.

    Immutability model:
      - Test rows (is_test_record=TRUE): DELETE and UPDATE freely permitted
        (supports clean test resets).
      - Production rows (is_test_record=FALSE):
          DELETE  → always blocked by trigger.
          UPDATE  → only verification_status may change; all other fields immutable.
    """
    conn = _conn(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_DPL_TABLE} (
                    decision_id         TEXT        PRIMARY KEY,
                    parent_id           TEXT        REFERENCES {_DPL_TABLE}(decision_id),
                    created_at          TIMESTAMPTZ NOT NULL
                                        DEFAULT (NOW() AT TIME ZONE 'UTC'),
                    input_hash          TEXT        NOT NULL,
                    output_hash         TEXT        NOT NULL,
                    verification_status TEXT        NOT NULL DEFAULT 'PENDING'
                                        CHECK (verification_status
                                               IN ('VERIFIED', 'PENDING', 'TAMPERED')),
                    engine_version      TEXT        NOT NULL,
                    db_version          TEXT        NOT NULL,
                    is_test_record      BOOLEAN     NOT NULL DEFAULT FALSE
                )
            """)

            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_dpl_guard_immutability()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    -- Test records: permit DELETE and UPDATE freely
                    IF TG_OP = 'DELETE' THEN
                        IF OLD.is_test_record THEN
                            RETURN OLD;
                        END IF;
                        RAISE EXCEPTION
                            'oe_decision_audit is append-only: '
                            'DELETE not permitted on production rows';
                    END IF;

                    -- UPDATE: test records unrestricted
                    IF OLD.is_test_record THEN
                        RETURN NEW;
                    END IF;

                    -- UPDATE: production records — core fields are immutable
                    IF NEW.decision_id    IS DISTINCT FROM OLD.decision_id    OR
                       NEW.parent_id      IS DISTINCT FROM OLD.parent_id      OR
                       NEW.created_at     IS DISTINCT FROM OLD.created_at     OR
                       NEW.input_hash     IS DISTINCT FROM OLD.input_hash     OR
                       NEW.output_hash    IS DISTINCT FROM OLD.output_hash    OR
                       NEW.engine_version IS DISTINCT FROM OLD.engine_version OR
                       NEW.db_version     IS DISTINCT FROM OLD.db_version     OR
                       NEW.is_test_record IS DISTINCT FROM OLD.is_test_record
                    THEN
                        RAISE EXCEPTION
                            'oe_decision_audit: core fields are immutable '
                            '(only verification_status may be updated on production rows)';
                    END IF;
                    RETURN NEW;
                END;
                $$
            """)

            cur.execute(
                "DROP TRIGGER IF EXISTS trg_oe_dpl_immutable ON oe_decision_audit"
            )
            cur.execute("""
                CREATE TRIGGER trg_oe_dpl_immutable
                BEFORE UPDATE OR DELETE ON oe_decision_audit
                FOR EACH ROW EXECUTE FUNCTION _oe_dpl_guard_immutability()
            """)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_decision(
    input_data:     dict,
    output_data:    dict,
    parent_id:      str  = None,
    is_test_record: bool = False,
    db_url:         str  = None,
) -> dict:
    """
    Append a new decision audit row.

    Reject-on-integrity-failure gate: immediately after INSERT the stored hashes
    are re-read and compared against computed values. Mismatch → rollback +
    ValueError (never silently accepted).

    Returns dict with decision_id, parent_id, input_hash, output_hash,
    engine_version, db_version, verification_status='VERIFIED'.
    """
    conn = _conn(db_url)
    try:
        input_hash  = _sha256(input_data)
        output_hash = _sha256(output_data)
        decision_id = uuid.uuid4().hex[:24]

        with conn.cursor() as cur:
            eng_ver = _live_engine_version(cur)
            db_ver  = _live_db_version(cur)

            cur.execute(f"""
                INSERT INTO {_DPL_TABLE}
                    (decision_id, parent_id, created_at,
                     input_hash, output_hash, verification_status,
                     engine_version, db_version, is_test_record)
                VALUES (%s, %s, NOW() AT TIME ZONE 'UTC',
                        %s, %s, 'PENDING',
                        %s, %s, %s)
            """, (decision_id, parent_id,
                  input_hash, output_hash,
                  eng_ver, db_ver, is_test_record))

            _post_write_integrity_check(cur, decision_id, input_hash, output_hash)

            cur.execute(
                f"UPDATE {_DPL_TABLE} SET verification_status = 'VERIFIED' "
                "WHERE decision_id = %s",
                (decision_id,)
            )

        conn.commit()
        return {
            "decision_id":         decision_id,
            "parent_id":           parent_id,
            "input_hash":          input_hash,
            "output_hash":         output_hash,
            "engine_version":      eng_ver,
            "db_version":          db_ver,
            "verification_status": "VERIFIED",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def amend_decision(
    original_decision_id: str,
    new_input_data:       dict,
    new_output_data:      dict,
    is_test_record:       bool = False,
    db_url:               str  = None,
) -> dict:
    """
    'Update' a decision by inserting a new row referencing the original as parent.
    The original row is NOT modified. Returns the new row dict.
    """
    return write_decision(
        input_data      = new_input_data,
        output_data     = new_output_data,
        parent_id       = original_decision_id,
        is_test_record  = is_test_record,
        db_url          = db_url,
    )


def verify_decision(
    decision_id: str,
    input_data:  dict,
    output_data: dict,
    db_url:      str = None,
) -> dict:
    """
    Recompute hashes from provided data and compare against stored values.
    Updates verification_status to VERIFIED or TAMPERED.
    Returns dict with 'status', 'decision_id', 'input_match', 'output_match'.
    """
    conn = _conn(db_url)
    try:
        computed_input  = _sha256(input_data)
        computed_output = _sha256(output_data)

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT input_hash, output_hash FROM {_DPL_TABLE} "
                "WHERE decision_id = %s",
                (decision_id,)
            )
            row = cur.fetchone()

        if row is None:
            return {"status": "NOT_FOUND", "decision_id": decision_id,
                    "input_match": False, "output_match": False}

        stored_input, stored_output = row
        input_match  = computed_input  == stored_input
        output_match = computed_output == stored_output
        status = "VERIFIED" if (input_match and output_match) else "TAMPERED"

        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_DPL_TABLE} SET verification_status = %s "
                "WHERE decision_id = %s",
                (status, decision_id)
            )
        conn.commit()
        return {
            "status":       status,
            "decision_id":  decision_id,
            "input_match":  input_match,
            "output_match": output_match,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
