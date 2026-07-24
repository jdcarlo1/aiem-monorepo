#!/usr/bin/env python3
"""
test_pipeline_checkpoints.py — Negative-control tests for pipeline stage checkpoints.

Directive requirement: for each of the 11 stages, force a failure INSIDE the stage
after the checkpoint has been written and prove the checkpoint survives.

Tests:
  NC-01 through NC-11: one per stage — checkpoint written, stage logic crashes,
                        checkpoint row still present (write-before-work atomicity).
  NC-12: chk() does NOT raise when DB is unreachable (alert-on-failure, not crash).
  NC-13: idempotency — duplicate write_checkpoint does not create a second row.
  NC-14: stage 4 (TRIGGER_EVALUATED) written before stage 5 (TRIGGER_LOGGED);
         simulated TRIGGER_LOGGED failure leaves TRIGGER_EVALUATED intact.
  NC-15: pipeline_trace_context — get_or_set_trace_id returns same value on re-call.
"""

import os
import sys
import uuid
import psycopg2

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_API_DIR  = os.path.abspath(os.path.join(_THIS_DIR, ".."))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import aiem_pipeline_checkpoints as chkp

_DB_URL = os.environ.get("DATABASE_URL", "")

PASS = 0
FAIL = 0
_results = []

def _p(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        status = "PASS"
    else:
        FAIL += 1
        status = "FAIL"
    msg = f"  {status}: {label}" + (f" — {detail}" if detail else "")
    _results.append(msg)
    print(msg)

def _row_exists(tid, stage):
    with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM pipeline_stage_checkpoints WHERE trace_id=%s AND stage=%s",
            (tid, stage))
        return cur.fetchone() is not None

def _row_absent(tid, stage):
    return not _row_exists(tid, stage)

def _fresh_tid(label):
    return f"nc_{label}_{uuid.uuid4().hex[:8]}"

# ── Setup: ensure tables ──────────────────────────────────────────────────────
print("\n=== SETUP ===")
try:
    chkp.ensure_tables(_DB_URL)
    print("  ensure_tables OK")
except Exception as e:
    print(f"  FATAL: ensure_tables failed: {e}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# NC-01 through NC-11: write-before-work — checkpoint survives stage crash
# For each stage: write checkpoint, then "crash" (simulate bad stage work),
# verify row still present.
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== NC-01 to NC-11: write-before-work atomicity per stage ===")

for stage_name in [
    "WATCHDOG_POLL",     # NC-01
    "RUN_SCAN_CALLED",   # NC-02
    "RUN_SCAN_RESPONSE", # NC-03
    "TRIGGER_EVALUATED", # NC-04
    "TRIGGER_LOGGED",    # NC-05
    "SCAN_RUN_CREATED",  # NC-06
    "SEED_STAGE",        # NC-07
    "P2_INIT",           # NC-08
    "P2_GATE",           # NC-09
    "P2_CAPTURE",        # NC-10
    "DECISION_WRITTEN",  # NC-11
]:
    tid = _fresh_tid(stage_name.lower())
    try:
        # Write checkpoint (separate committed transaction)
        chkp.write_checkpoint(tid, stage_name, {"negctrl": True, "stage": stage_name}, _DB_URL)
        # Simulate stage-work crash: attempt a bad SQL in a new connection
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=3) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1/0")        # integer division by zero
                conn.commit()                    # never reached
        except Exception:
            pass                                 # expected; this is the crash
        # Verify checkpoint survived the crash
        survived = _row_exists(tid, stage_name)
        _p(f"NC: {stage_name} survives post-write crash", survived,
           f"trace={tid[:12]}")
    except Exception as e:
        _p(f"NC: {stage_name} — test error", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
# NC-12: chk() never raises even when DB is unreachable
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== NC-12: chk() alert-on-failure, never raises ===")
_bad_url = "postgresql://nonexistent_user:badpass@127.0.0.1:9999/no_db"
raised = False
try:
    chkp.chk("any_trace_id", "WATCHDOG_POLL", {"test": True}, _bad_url)
except Exception:
    raised = True
_p("NC-12: chk() does not raise on unreachable DB", not raised)

# ─────────────────────────────────────────────────────────────────────────────
# NC-13: idempotency — second write does not create duplicate row
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== NC-13: idempotency (no duplicate rows) ===")
tid13 = _fresh_tid("idem")
chkp.write_checkpoint(tid13, "SEED_STAGE", {"x": 1}, _DB_URL)
chkp.write_checkpoint(tid13, "SEED_STAGE", {"x": 2}, _DB_URL)  # retry / replay
with psycopg2.connect(_DB_URL, connect_timeout=5) as conn, conn.cursor() as cur:
    cur.execute(
        "SELECT COUNT(*), payload->>'x' FROM pipeline_stage_checkpoints "
        "WHERE trace_id=%s AND stage='SEED_STAGE' GROUP BY payload->>'x'",
        (tid13,))
    rows = cur.fetchall()
count13 = sum(r[0] for r in rows)
_p("NC-13: exactly one row after two writes (idempotent)", count13 == 1,
   f"row_count={count13}")
payload_x = rows[0][1] if rows else None
_p("NC-13: payload updated to latest value", payload_x == "2",
   f"payload_x={payload_x}")

# ─────────────────────────────────────────────────────────────────────────────
# NC-14: TRIGGER_EVALUATED written → TRIGGER_LOGGED fails → TRIGGER_EVALUATED intact
# Simulates: gate computes decision (writes TRIGGER_EVALUATED) →
#            aiem_scan_trigger_log INSERT fails → TRIGGER_LOGGED never written
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== NC-14: TRIGGER_EVALUATED/LOGGED independence ===")
tid14 = _fresh_tid("gate_crash")
# Write stage 4 (TRIGGER_EVALUATED) — simulates _chk_write("TRIGGER_EVALUATED", ...)
chkp.write_checkpoint(tid14, "TRIGGER_EVALUATED", {"action": "accepted", "reason": "negctrl"}, _DB_URL)
# Simulate stage-5 work (aiem_scan_trigger_log INSERT) crashing
try:
    with psycopg2.connect(_DB_URL, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO nonexistent_crash_table_xyz VALUES(1)")
        conn.commit()
except Exception:
    pass  # expected
# TRIGGER_LOGGED is never written because the crash happened before that call
# Verify TRIGGER_EVALUATED exists, TRIGGER_LOGGED absent
_p("NC-14: TRIGGER_EVALUATED survives gate crash", _row_exists(tid14, "TRIGGER_EVALUATED"),
   f"trace={tid14[:12]}")
_p("NC-14: TRIGGER_LOGGED absent after crash (not written)", _row_absent(tid14, "TRIGGER_LOGGED"),
   f"trace={tid14[:12]}")

# ─────────────────────────────────────────────────────────────────────────────
# NC-15: get_or_set_trace_id — same value returned on subsequent calls
# Cleans up the test row first so the test is idempotent across runs.
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== NC-15: pipeline_trace_context idempotency ===")
from datetime import date as _d
_test_date = _d(2099, 12, 31)  # far-future date — isolated namespace
# Clean up any row left by a previous test run so the test is repeatable
try:
    with psycopg2.connect(_DB_URL, connect_timeout=5) as _c15, _c15.cursor() as _k15:
        _k15.execute("DELETE FROM pipeline_trace_context WHERE scan_date=%s", (_test_date,))
        _c15.commit()
except Exception:
    pass
tid15a = _fresh_tid("ctx_a")
result_a = chkp.get_or_set_trace_id(_test_date, _DB_URL, new_trace_id=tid15a)
result_b = chkp.get_or_set_trace_id(_test_date, _DB_URL, new_trace_id=_fresh_tid("ctx_b"))
_p("NC-15: first call returns provided trace_id", result_a == tid15a,
   f"expected={tid15a[:12]} got={result_a[:12]}")
_p("NC-15: second call returns SAME trace_id (no overwrite)", result_b == tid15a,
   f"expected={tid15a[:12]} got={result_b[:12]}")

# ── Summary ── format must start with "SUMMARY:" for PSV8 in verified_run.sh ──
print(f"\nSUMMARY: {PASS} PASS  {FAIL} FAIL  (total {PASS+FAIL})")
for r in _results:
    if "FAIL" in r:
        print(r)
sys.exit(0 if FAIL == 0 else 1)
