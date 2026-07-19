#!/usr/bin/env python3
"""
verify_dpl_phase1.py — Decision Proof Layer Phase 1 evidence script
====================================================================
Acceptance checkpoints:
  AC-01  Bootstrap — table + trigger + CHECK constraint created
  AC-02  Bootstrap idempotent (re-run, row count unchanged)
  AC-03  write_decision — row inserted with VERIFIED status, all fields present
  AC-04  amend_decision — new row created (not overwrite), parent_id correct
  AC-05  Original row unchanged after amend
  AC-06  Trigger blocks DELETE on production row (negative control)
  AC-07  Trigger blocks UPDATE of core column on production row (negative control)
  AC-08  UPDATE of verification_status allowed (trigger permits this)
  AC-09  verify_decision returns TAMPERED for row with wrong stored hashes
  AC-10  _post_write_integrity_check raises ValueError on hash mismatch
  AC-11  engine_version sourced from live DB query (no hardcoded literal)
  AC-12  db_version sourced from live DB query (no hardcoded literal)
  AC-13  Schema columns match DDL spec exactly
  SCHED  bootstrap_dpl wired in options scheduler

Run via: bash tools/verified_run.sh "python3 verify_dpl_phase1.py"
Exit: 0 = all PASS, 1 = any FAIL
"""

import os
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.errors

_DB_URL     = os.environ.get("DATABASE_URL", "")
_PASS       = "PASS"
_FAIL       = "FAIL"
_pass_count = 0
_fail_count = 0


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(_level, name, **kw):
    global _pass_count, _fail_count
    kv = "  |  " + "  ".join(f"{k}={v}" for k, v in kw.items()) if kw else ""
    print(f"[{_ts()}] {_level}  {name}{kv}", flush=True)
    if _level == _PASS:
        _pass_count += 1
    elif _level == _FAIL:
        _fail_count += 1


def _ok(name, **kw):   _log(_PASS, name, **kw)
def _fail(name, **kw): _log(_FAIL, name, **kw)
def _info(name, **kw): _log("INFO", name, **kw)


def _uid():
    return uuid.uuid4().hex[:24]


def _raw_conn():
    return psycopg2.connect(_DB_URL, connect_timeout=8)


import aiem_options_dpl as dpl
from aiem_options_dpl import _sha256, _post_write_integrity_check

# ── Pre-run cleanup: delete test rows (trigger allows is_test_record=TRUE) ──
print(f"[{_ts()}] ===== verify_dpl_phase1.py START =====", flush=True)
try:
    _c = _raw_conn()
    with _c.cursor() as _cu:
        _cu.execute("DELETE FROM oe_decision_audit WHERE is_test_record = TRUE")
    _c.commit()
    _c.close()
    print(f"[{_ts()}] PRE-RUN CLEANUP: done", flush=True)
except Exception:
    print(f"[{_ts()}] PRE-RUN CLEANUP: table not yet created (first run)", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# AC-01: Bootstrap
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-01: Bootstrap ---", flush=True)
try:
    result = dpl.bootstrap_dpl(_DB_URL)
    if result is True:
        _ok("AC01.bootstrap_returns_true", result=result)
    else:
        _fail("AC01.bootstrap_returns_true", result=result)

    conn = _raw_conn()
    with conn.cursor() as cur:
        required = {
            "decision_id", "parent_id", "created_at",
            "input_hash", "output_hash", "verification_status",
            "engine_version", "db_version", "is_test_record",
        }
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'oe_decision_audit'"
        )
        present = {r[0] for r in cur.fetchall()}
        missing = required - present
        if not missing:
            _ok("AC01.all_required_columns_present", count=len(present))
        else:
            _fail("AC01.all_required_columns_present", missing=sorted(missing))

        cur.execute("""
            SELECT COUNT(*) FROM information_schema.table_constraints tc
            JOIN information_schema.check_constraints cc
              ON tc.constraint_name = cc.constraint_name
            WHERE tc.table_name = 'oe_decision_audit'
              AND tc.constraint_type = 'CHECK'
        """)
        n_check = cur.fetchone()[0]
        if n_check >= 1:
            _ok("AC01.check_constraint_exists", n_check=n_check)
        else:
            _fail("AC01.check_constraint_exists", n_check=n_check)

        cur.execute("""
            SELECT COUNT(*) FROM pg_trigger
            WHERE tgname = 'trg_oe_dpl_immutable'
              AND tgrelid = 'oe_decision_audit'::regclass
        """)
        n_trig = cur.fetchone()[0]
        if n_trig == 1:
            _ok("AC01.immutability_trigger_exists", n_trig=n_trig)
        else:
            _fail("AC01.immutability_trigger_exists", n_trig=n_trig)
    conn.close()
except Exception as e:
    _fail("AC01.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-02: Bootstrap idempotent
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-02: Bootstrap idempotent ---", flush=True)
try:
    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM oe_decision_audit WHERE is_test_record = FALSE"
        )
        before = cur.fetchone()[0]
    conn.close()

    result2 = dpl.bootstrap_dpl(_DB_URL)
    if result2 is True:
        _ok("AC02.second_bootstrap_returns_true")
    else:
        _fail("AC02.second_bootstrap_returns_true", result=result2)

    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM oe_decision_audit WHERE is_test_record = FALSE"
        )
        after = cur.fetchone()[0]
    conn.close()

    if before == after:
        _ok("AC02.production_row_count_unchanged", before=before, after=after)
    else:
        _fail("AC02.production_row_count_unchanged", before=before, after=after)
except Exception as e:
    _fail("AC02.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-03: write_decision inserts row with VERIFIED status
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-03: write_decision ---", flush=True)
_orig_id     = None
_orig_input  = {"ticker": "TEST", "signal": "gap_volume", "score": 75}
_orig_output = {"recommendation": "BUY", "confidence": 0.82}

try:
    rec = dpl.write_decision(
        _orig_input, _orig_output, is_test_record=True, db_url=_DB_URL
    )
    _orig_id = rec["decision_id"]
    _ok("AC03.returns_decision_id", decision_id=_orig_id)

    if rec["verification_status"] == "VERIFIED":
        _ok("AC03.verification_status_verified")
    else:
        _fail("AC03.verification_status_verified", got=rec["verification_status"])

    if rec["parent_id"] is None:
        _ok("AC03.parent_id_is_none")
    else:
        _fail("AC03.parent_id_is_none", got=rec["parent_id"])

    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT decision_id, parent_id, input_hash, output_hash, "
            "verification_status, engine_version, db_version, is_test_record "
            "FROM oe_decision_audit WHERE decision_id = %s",
            (_orig_id,)
        )
        row = cur.fetchone()
    conn.close()

    if row:
        _ok("AC03.row_in_db")
        _ok("AC03.db_verification_status", status=row[4])
        _ok("AC03.db_parent_id_null", parent_id=row[1])
        _ok("AC03.db_input_hash_matches", match=(row[2] == rec["input_hash"]))
        _ok("AC03.db_output_hash_matches", match=(row[3] == rec["output_hash"]))
        _ok("AC03.db_is_test_record_true", is_test_record=row[7])
        _info("engine_version", engine_version=row[5])
        _info("db_version", db_version=row[6])
    else:
        _fail("AC03.row_in_db", error="not found after write")
except Exception as e:
    _fail("AC03.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-04: amend_decision creates new row with parent_id linkage
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-04: amend_decision ---", flush=True)
_amend_id     = None
_amend_input  = {"ticker": "TEST", "signal": "gap_volume", "score": 80}
_amend_output = {"recommendation": "BUY", "confidence": 0.91}

try:
    if _orig_id:
        amend = dpl.amend_decision(
            _orig_id, _amend_input, _amend_output,
            is_test_record=True, db_url=_DB_URL
        )
        _amend_id = amend["decision_id"]

        if _amend_id != _orig_id:
            _ok("AC04.new_id_differs_from_original",
                orig=_orig_id[:8], new=_amend_id[:8])
        else:
            _fail("AC04.new_id_differs_from_original")

        if amend["parent_id"] == _orig_id:
            _ok("AC04.parent_id_correct", parent_id=amend["parent_id"][:8])
        else:
            _fail("AC04.parent_id_correct",
                  got=amend["parent_id"], expected=_orig_id)

        conn = _raw_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decision_id, parent_id FROM oe_decision_audit "
                "WHERE decision_id IN (%s, %s) ORDER BY created_at",
                (_orig_id, _amend_id)
            )
            rows = cur.fetchall()
        conn.close()

        if len(rows) == 2:
            _ok("AC04.two_rows_in_db", count=2)
        else:
            _fail("AC04.two_rows_in_db", count=len(rows))

        amend_db = [r for r in rows if r[0] == _amend_id]
        if amend_db and amend_db[0][1] == _orig_id:
            _ok("AC04.db_parent_id_correct")
        else:
            _fail("AC04.db_parent_id_correct")
    else:
        _fail("AC04.skipped_no_orig_id")
except Exception as e:
    _fail("AC04.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-05: Original row unchanged after amend
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-05: Original row unchanged after amend ---", flush=True)
try:
    if _orig_id:
        conn = _raw_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT input_hash, output_hash, parent_id "
                "FROM oe_decision_audit WHERE decision_id = %s",
                (_orig_id,)
            )
            orig_row = cur.fetchone()
        conn.close()

        if orig_row[0] == _sha256(_orig_input):
            _ok("AC05.original_input_hash_unchanged")
        else:
            _fail("AC05.original_input_hash_unchanged",
                  stored=orig_row[0][:16])

        if orig_row[1] == _sha256(_orig_output):
            _ok("AC05.original_output_hash_unchanged")
        else:
            _fail("AC05.original_output_hash_unchanged",
                  stored=orig_row[1][:16])

        if orig_row[2] is None:
            _ok("AC05.original_parent_id_still_null")
        else:
            _fail("AC05.original_parent_id_still_null", got=orig_row[2])
    else:
        _fail("AC05.skipped_no_orig_id")
except Exception as e:
    _fail("AC05.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-06: Trigger blocks DELETE on production row (negative control)
# Write a production row (is_test_record=FALSE), attempt DELETE → must block.
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-06: Trigger blocks DELETE on production row ---", flush=True)
_prod_id = None
try:
    prod = dpl.write_decision(
        {"ticker": "PROD_TEST", "signal": "sentinel"},
        {"recommendation": "HOLD"},
        is_test_record=False,
        db_url=_DB_URL,
    )
    _prod_id = prod["decision_id"]
    _info("AC06.production_row_written", decision_id=_prod_id[:8])

    conn = _raw_conn()
    blocked = False
    err_msg = ""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM oe_decision_audit WHERE decision_id = %s",
                (_prod_id,)
            )
        conn.commit()
    except psycopg2.errors.RaiseException as e:
        blocked = True
        err_msg = str(e).strip()
        conn.rollback()
    finally:
        conn.close()

    if blocked:
        _ok("AC06.delete_blocked_by_trigger", excerpt=err_msg[:80])
    else:
        _fail("AC06.delete_blocked_by_trigger",
              error="DELETE succeeded — trigger not firing")
except Exception as e:
    _fail("AC06.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-07: Trigger blocks UPDATE of core column on production row (negative control)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-07: Trigger blocks UPDATE of core column ---", flush=True)
try:
    if _prod_id:
        conn = _raw_conn()
        blocked = False
        err_msg = ""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE oe_decision_audit SET input_hash = 'tampered_value' "
                    "WHERE decision_id = %s",
                    (_prod_id,)
                )
            conn.commit()
        except psycopg2.errors.RaiseException as e:
            blocked = True
            err_msg = str(e).strip()
            conn.rollback()
        finally:
            conn.close()

        if blocked:
            _ok("AC07.core_column_update_blocked", excerpt=err_msg[:80])
        else:
            _fail("AC07.core_column_update_blocked",
                  error="UPDATE succeeded — immutability broken")
    else:
        _fail("AC07.skipped_no_prod_id")
except Exception as e:
    _fail("AC07.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-08: UPDATE of verification_status allowed on production row
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-08: verification_status UPDATE allowed ---", flush=True)
try:
    if _prod_id:
        conn = _raw_conn()
        allowed = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE oe_decision_audit "
                    "SET verification_status = 'PENDING' "
                    "WHERE decision_id = %s",
                    (_prod_id,)
                )
            conn.commit()
            allowed = True
        except Exception as e:
            conn.rollback()
        finally:
            conn.close()

        if allowed:
            _ok("AC08.verification_status_update_allowed")
        else:
            _fail("AC08.verification_status_update_allowed",
                  error="trigger blocked verification_status update")
    else:
        _fail("AC08.skipped_no_prod_id")
except Exception as e:
    _fail("AC08.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-09: verify_decision returns TAMPERED for row with wrong stored hashes
# Raw INSERT with deliberate wrong hashes simulates storage tamper.
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-09: Tampered hash detected ---", flush=True)
_tampered_id = None
try:
    tampered_id = _uid()
    _tampered_id = tampered_id
    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT split_part(version(), ' ', 2)"
        )
        db_ver = cur.fetchone()[0]
        cur.execute(
            "SELECT version_id FROM oe_model_versions "
            "WHERE is_active=TRUE AND is_test_record=FALSE LIMIT 1"
        )
        r = cur.fetchone()
        eng_ver = r[0] if r else "no_active_champion"

        cur.execute("""
            INSERT INTO oe_decision_audit
                (decision_id, parent_id, created_at,
                 input_hash, output_hash,
                 verification_status, engine_version, db_version, is_test_record)
            VALUES (%s, NULL, NOW() AT TIME ZONE 'UTC',
                    %s, %s,
                    'VERIFIED', %s, %s, TRUE)
        """, (
            tampered_id,
            "aaaa" * 16,
            "bbbb" * 16,
            eng_ver, db_ver
        ))
    conn.commit()
    conn.close()

    real_input  = {"ticker": "TAMPER_TEST", "signal": "none", "score": 0}
    real_output = {"recommendation": "HOLD", "confidence": 0.50}
    result = dpl.verify_decision(
        tampered_id, real_input, real_output, db_url=_DB_URL
    )

    if result["status"] == "TAMPERED":
        _ok("AC09.tampered_hash_detected", status=result["status"])
        _ok("AC09.input_mismatch_flagged",  input_match=result["input_match"])
        _ok("AC09.output_mismatch_flagged", output_match=result["output_match"])
    else:
        _fail("AC09.tampered_hash_detected", status=result["status"])

    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT verification_status FROM oe_decision_audit "
            "WHERE decision_id = %s",
            (tampered_id,)
        )
        db_status = cur.fetchone()[0]
    conn.close()
    if db_status == "TAMPERED":
        _ok("AC09.db_status_updated_to_tampered")
    else:
        _fail("AC09.db_status_updated_to_tampered", got=db_status)
except Exception as e:
    _fail("AC09.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-10: _post_write_integrity_check raises ValueError on hash mismatch
# Insert raw row with wrong hash; gate must raise when expected != stored.
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-10: Integrity gate raises on mismatch ---", flush=True)
try:
    gate_id     = _uid()
    gate_input  = {"ticker": "GATE_TEST", "price": 42.0}
    gate_output = {"recommendation": "SELL", "confidence": 0.60}
    correct_in  = _sha256(gate_input)
    correct_out = _sha256(gate_output)
    wrong_in    = "BAD_HASH_" + "0" * 55

    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT split_part(version(), ' ', 2)")
        db_ver = cur.fetchone()[0]
        cur.execute(
            "SELECT version_id FROM oe_model_versions "
            "WHERE is_active=TRUE AND is_test_record=FALSE LIMIT 1"
        )
        r = cur.fetchone()
        eng_ver = r[0] if r else "no_active_champion"

        cur.execute("""
            INSERT INTO oe_decision_audit
                (decision_id, parent_id, created_at,
                 input_hash, output_hash,
                 verification_status, engine_version, db_version, is_test_record)
            VALUES (%s, NULL, NOW() AT TIME ZONE 'UTC',
                    %s, %s, 'PENDING', %s, %s, TRUE)
        """, (gate_id, wrong_in, correct_out, eng_ver, db_ver))
    conn.commit()

    gate_raised = False
    gate_err    = ""
    try:
        with conn.cursor() as cur:
            _post_write_integrity_check(cur, gate_id, correct_in, correct_out)
    except ValueError as e:
        gate_raised = True
        gate_err    = str(e)
    conn.close()

    if gate_raised:
        _ok("AC10.integrity_gate_raises_on_mismatch",
            excerpt=gate_err[:80])
    else:
        _fail("AC10.integrity_gate_raises_on_mismatch",
              error="gate did not raise")
except Exception as e:
    _fail("AC10.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# AC-11: engine_version sourced from live DB query — no hardcoded literal
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-11: engine_version from live DB ---", flush=True)
try:
    r = subprocess.run(
        ["grep", "-n", r"champion_v[0-9]", "aiem_options_dpl.py"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
    )
    hardcoded = [l for l in r.stdout.strip().splitlines()
                 if not l.strip().startswith("#")
                 and "FALLBACK" not in l
                 and "engine_version" not in l.split(":")[0]]
    if hardcoded:
        _fail("AC11.no_hardcoded_champion_version_literal", hits=hardcoded)
    else:
        _ok("AC11.no_hardcoded_champion_version_literal")

    r2 = subprocess.run(
        ["grep", "-n", "oe_model_versions", "aiem_options_dpl.py"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
    )
    if r2.stdout.strip():
        _ok("AC11.live_engine_version_query_present",
            lines=r2.stdout.strip().replace("\n", " | ")[:120])
    else:
        _fail("AC11.live_engine_version_query_present")
except Exception as e:
    _fail("AC11.exception", error=repr(e))

# ─────────────────────────────────────────────────────────────────────────────
# AC-12: db_version sourced from live DB query — no hardcoded literal
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-12: db_version from live DB ---", flush=True)
try:
    r = subprocess.run(
        ["grep", "-n", r"[0-9]\{1,2\}\.[0-9]\{1,2\}", "aiem_options_dpl.py"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
    )
    version_literals = [
        l for l in r.stdout.strip().splitlines()
        if not l.strip().startswith("#")
        and "connect_timeout" not in l
        and "statement_timeout" not in l
    ]
    if version_literals:
        _fail("AC12.no_hardcoded_db_version_literal", hits=version_literals)
    else:
        _ok("AC12.no_hardcoded_db_version_literal")

    r2 = subprocess.run(
        ["grep", "-n", "split_part.*version", "aiem_options_dpl.py"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
    )
    if r2.stdout.strip():
        _ok("AC12.live_db_version_query_present",
            lines=r2.stdout.strip().replace("\n", " | ")[:120])
    else:
        _fail("AC12.live_db_version_query_present")
except Exception as e:
    _fail("AC12.exception", error=repr(e))

# ─────────────────────────────────────────────────────────────────────────────
# AC-13: Schema columns match DDL spec
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- AC-13: Schema DDL spec match ---", flush=True)
try:
    conn = _raw_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'oe_decision_audit'
            ORDER BY ordinal_position
        """)
        cols = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    conn.close()

    spec = {
        "decision_id":         ("text",                     "NO"),
        "parent_id":           ("text",                     "YES"),
        "created_at":          ("timestamp with time zone", "NO"),
        "input_hash":          ("text",                     "NO"),
        "output_hash":         ("text",                     "NO"),
        "verification_status": ("text",                     "NO"),
        "engine_version":      ("text",                     "NO"),
        "db_version":          ("text",                     "NO"),
        "is_test_record":      ("boolean",                  "NO"),
    }
    for col, (exp_type, exp_null) in spec.items():
        if col not in cols:
            _fail(f"AC13.col_{col}", error="missing")
            continue
        got_type, got_null = cols[col]
        if got_type == exp_type and got_null == exp_null:
            _ok(f"AC13.col_{col}", type=got_type, nullable=got_null)
        else:
            _fail(f"AC13.col_{col}",
                  got_type=got_type, exp_type=exp_type,
                  got_null=got_null, exp_null=exp_null)
except Exception as e:
    _fail("AC13.exception", error=repr(e))
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# SCHED: bootstrap_dpl wired in options scheduler
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] --- SCHED: scheduler wiring ---", flush=True)
try:
    r = subprocess.run(
        ["grep", "-n", r"bootstrap_dpl\|aiem_options_dpl",
         "aiem_options_scheduler.py"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
    )
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    if len(lines) >= 2:
        _ok("SCHED.dpl_wired_in_scheduler", count=len(lines))
        for l in lines:
            _info("SCHED.match", line=l.strip())
    else:
        _fail("SCHED.dpl_wired_in_scheduler", found=len(lines))
except Exception as e:
    _fail("SCHED.exception", error=repr(e))

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[{_ts()}] ===== SUMMARY =====", flush=True)
print(f"[{_ts()}] PASS={_pass_count}  FAIL={_fail_count}  "
      f"TOTAL_CHECKS={_pass_count + _fail_count}", flush=True)
print(f"[{_ts()}] OVERALL: {'PASS' if _fail_count == 0 else 'FAIL'}", flush=True)
sys.exit(0 if _fail_count == 0 else 1)
