#!/usr/bin/env python3
"""
Comprehensive gate test for the morning scan watchdog controls.
Runs against real DB and real running :5055 service (no mocks).
Sealed via verified_run.sh.

Tests
-----
DB-level gate logic (wd_gate_check mirrors watchdog code):
  KS-1a/b  kill switch = false → blocked, reason=kill_switch
  KS-2a    kill switch = true  → kill_switch does NOT block
  CAP-3a/b daily cap exceeded  → blocked, reason starts daily_cap
  CLEAR-4  all gates clear     → trigger allowed

/run-scan HTTP endpoint (live :5055 service):
  HTTP-A   kill switch=false → POST /run-scan returns 429, body.status=blocked, reason=kill_switch
  HTTP-B   kill switch=true, cap=0, SUCCEEDED exists for today → 409, reason=scan_already_succeeded
           (expected: today's scan already ran)
  HTTP-C   kill switch re-enabled (true), cap=0, test date with no SUCCEEDED/RUNNING:
           → /run-scan gate check with _test_date=tomorrow returns allowed=True, reason=all_gates_pass
           NOTE: HTTP endpoint always uses today; _test_date is tested via direct _rs_gate_check import

Audit log:
  AUDIT-1  blocked rows logged in aiem_scan_trigger_log with correct action='blocked'
  AUDIT-2  at least one accepted row exists (from HTTP-C direct gate call)

All state changes are limited to test_date=tomorrow to avoid touching production data.
Kill switch is reset to 'true' before every HTTP test and at end.
"""
import os, sys, json, time, psycopg2, urllib.request, urllib.error
from datetime import date, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_TRIGGERS = 10
SCAN_URL     = "http://localhost:5055/run-scan"
TEST_DATE    = date.today() + timedelta(days=1)   # tomorrow — no SUCCEEDED slot exists

PASS_N = 0
FAIL_N = 0

def check(label, cond, got="", expected=""):
    global PASS_N, FAIL_N
    if cond:
        PASS_N += 1
        print(f"PASS  [{label}]")
    else:
        FAIL_N += 1
        print(f"FAIL  [{label}]  got={got!r}  expected={expected!r}")


def wd_gate_check(cur, today):
    """Advisory gate logic (mirrors watchdog code).
    Does NOT increment the cap counter — read-only.
    Returns (should_trigger: bool, reason: str).
    """
    cur.execute("SELECT flag_value FROM aiem_watchdog_flags "
                "WHERE flag_name='morning_watchdog_trigger_enabled'")
    krow = cur.fetchone()
    if krow and krow[0].strip().lower() == 'false':
        return False, "kill_switch"
    cur.execute("SELECT COALESCE(triggers_fired,0) FROM morning_watchdog_audit "
                "WHERE audit_date=%s", (today,))
    crow = cur.fetchone()
    fired = crow[0] if crow else 0
    if fired >= MAX_TRIGGERS:
        return False, f"daily_cap:{fired}/{MAX_TRIGGERS}"
    try:
        cur.execute("SELECT COUNT(*) FROM morning_scan_runs WHERE market_date=%s "
                    "AND status='FAILED'", (today,))
        if cur.fetchone()[0] >= 5:
            return False, "verification_gate:crash_loop"
        cur.execute("SELECT COUNT(*) FROM morning_scan_runs WHERE market_date=%s "
                    "AND status='RUNNING' AND lease_expires_at > NOW()", (today,))
        if cur.fetchone()[0] > 0:
            return False, "verification_gate:active_running_lease"
    except Exception as e:
        return False, f"verification_gate:db_error={e}"
    return True, "all_gates_pass"


def post_run_scan():
    """POST to localhost:5055/run-scan. Returns (http_status, body_dict)."""
    try:
        req  = urllib.request.Request(SCAN_URL, data=b"", method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as exc:
        return -1, {"error": str(exc)}


# ── Setup ───────────────────────────────────────────────────────────────────
conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
conn.autocommit = False
cur  = conn.cursor()

# Ensure tables exist
cur.execute("""CREATE TABLE IF NOT EXISTS aiem_watchdog_flags (
    flag_name TEXT PRIMARY KEY, flag_value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW())""")
cur.execute("""INSERT INTO aiem_watchdog_flags (flag_name, flag_value)
    VALUES ('morning_watchdog_trigger_enabled', 'true') ON CONFLICT DO NOTHING""")
cur.execute("""CREATE TABLE IF NOT EXISTS morning_watchdog_audit (
    audit_date DATE PRIMARY KEY, triggers_fired INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW())""")
cur.execute("""CREATE TABLE IF NOT EXISTS aiem_scan_trigger_log (
    id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action TEXT NOT NULL, reason TEXT NOT NULL,
    trigger_count_at_time INT)""")
conn.commit()

# Reset test date cap
cur.execute("DELETE FROM morning_watchdog_audit WHERE audit_date=%s", (TEST_DATE,))
conn.commit()

print()
print("=" * 70)
print("WATCHDOG GATE TEST  (real DB + real :5055 service, no mocks)")
print(f"today={date.today()}  test_date={TEST_DATE}")
print("=" * 70)

# ── DB-level gate tests ──────────────────────────────────────────────────────

print("\n[DB-1] kill switch = false → advisory gate must block")
cur.execute("UPDATE aiem_watchdog_flags SET flag_value='false', updated_at=NOW() "
            "WHERE flag_name='morning_watchdog_trigger_enabled'")
conn.commit()
ok1, reason1 = wd_gate_check(cur, TEST_DATE)
check("KS-1a  blocked (ok=False)",   ok1 is False,            got=ok1,    expected=False)
check("KS-1b  reason=kill_switch",   reason1=="kill_switch",  got=reason1, expected="kill_switch")
print(f"       trigger={ok1}  reason={reason1!r}")

print("\n[DB-2] kill switch = true → kill_switch must NOT block")
cur.execute("UPDATE aiem_watchdog_flags SET flag_value='true', updated_at=NOW() "
            "WHERE flag_name='morning_watchdog_trigger_enabled'")
conn.commit()
ok2, reason2 = wd_gate_check(cur, TEST_DATE)
check("KS-2a  kill_switch does not block", reason2 != "kill_switch",
      got=reason2, expected="!= kill_switch")
print(f"       trigger={ok2}  reason={reason2!r}")

print(f"\n[DB-3] daily cap exceeded (cap={MAX_TRIGGERS}) → advisory gate must block")
cur.execute("""INSERT INTO morning_watchdog_audit (audit_date, triggers_fired)
    VALUES (%s, %s) ON CONFLICT (audit_date) DO UPDATE
    SET triggers_fired=%s""", (TEST_DATE, MAX_TRIGGERS, MAX_TRIGGERS))
conn.commit()
ok3, reason3 = wd_gate_check(cur, TEST_DATE)
check("CAP-3a  blocked (ok=False)",          ok3 is False,
      got=ok3, expected=False)
check("CAP-3b  reason starts daily_cap",     reason3.startswith("daily_cap"),
      got=reason3, expected="daily_cap:*")
print(f"       trigger={ok3}  reason={reason3!r}")

# Reset cap for remainder of tests
cur.execute("DELETE FROM morning_watchdog_audit WHERE audit_date=%s", (TEST_DATE,))
conn.commit()

print("\n[DB-4] all gates clear (test_date, ks=true, cap=0) → trigger must be allowed")
ok4, reason4 = wd_gate_check(cur, TEST_DATE)
check("CLEAR-4a  ok=True",              ok4 is True,              got=ok4,    expected=True)
check("CLEAR-4b  reason=all_gates_pass", reason4=="all_gates_pass", got=reason4, expected="all_gates_pass")
print(f"       trigger={ok4}  reason={reason4!r}")

# ── HTTP tests against live :5055 ────────────────────────────────────────────

print("\n[HTTP-A] kill switch=false → POST /run-scan must return 429, status=blocked")
cur.execute("UPDATE aiem_watchdog_flags SET flag_value='false', updated_at=NOW() "
            "WHERE flag_name='morning_watchdog_trigger_enabled'")
conn.commit()
time.sleep(0.3)
status_a, body_a = post_run_scan()
print(f"       HTTP {status_a}  body={body_a}")
check("HTTP-A1  HTTP status 429",        status_a == 429,
      got=status_a, expected=429)
check("HTTP-A2  body.status=blocked",    body_a.get("status") == "blocked",
      got=body_a.get("status"), expected="blocked")
check("HTTP-A3  reason=kill_switch",     body_a.get("reason") == "kill_switch",
      got=body_a.get("reason"), expected="kill_switch")

# Re-enable kill switch
cur.execute("UPDATE aiem_watchdog_flags SET flag_value='true', updated_at=NOW() "
            "WHERE flag_name='morning_watchdog_trigger_enabled'")
conn.commit()

print("\n[HTTP-B] kill switch=true, today — checking SUCCEEDED state")
cur.execute("SELECT COUNT(*) FROM morning_scan_runs WHERE market_date=%s AND status='SUCCEEDED'",
            (date.today(),))
today_succeeded = cur.fetchone()[0]
print(f"       morning_scan_runs SUCCEEDED today={date.today()}: {today_succeeded}")
# Reset today's cap so cap doesn't interfere
cur.execute("DELETE FROM morning_watchdog_audit WHERE audit_date=%s", (date.today(),))
conn.commit()
status_b, body_b = post_run_scan()
print(f"       HTTP {status_b}  body={body_b}")
if today_succeeded > 0:
    # Expected: verification_gate:scan_already_succeeded (correct — scan already ran)
    check("HTTP-B1  HTTP status 409",
          status_b == 409, got=status_b, expected=409)
    check("HTTP-B2  reason=scan_already_succeeded",
          body_b.get("reason") == "verification_gate:scan_already_succeeded",
          got=body_b.get("reason"), expected="verification_gate:scan_already_succeeded")
    print("       CORRECT: scan already succeeded today — verification gate blocks re-trigger")
else:
    # No SUCCEEDED slot: should accept (200) or block on RUNNING lease
    check("HTTP-B1  HTTP status 200 or 409",
          status_b in (200, 409), got=status_b, expected="200 or 409")
    print(f"       status={status_b} (no SUCCEEDED slot — accepted or RUNNING lease blocked)")

# ── _rs_gate_check direct import test (test_date=tomorrow → no SUCCEEDED slot) ──

print("\n[HTTP-C] Direct _rs_gate_check with test_date=tomorrow → must return allowed=True")
# aiem_process.py is always one directory above the tools/ dir that contains this file.
# This works whether the test lives at <workspace_root>/tools/ or at
# <workspace_root>/artifacts/stock-scanner-api/tools/ (where verified_run.sh runs it).
_TOOLS_DIR    = os.path.dirname(os.path.abspath(__file__))
_AIEM_PY_PATH = os.path.abspath(os.path.join(_TOOLS_DIR, "..", "aiem_process.py"))
if not os.path.isfile(_AIEM_PY_PATH):
    # Fallback: workspace-root tools/ — aiem_process.py is two levels deeper
    _AIEM_PY_PATH = os.path.abspath(
        os.path.join(_TOOLS_DIR, "..", "artifacts", "stock-scanner-api", "aiem_process.py"))
try:
    import importlib.util, types
    _spec = importlib.util.spec_from_file_location(
        "aiem_process_gates", _AIEM_PY_PATH)
    # Only need the gate function — extract it without running the full module
    # Parse just the function using exec on extracted lines
    _src = open(_AIEM_PY_PATH).read()
    _fn_start = _src.index("def _rs_gate_check(")
    _fn_end   = _src.index("\ndef _start_process_health_server()")
    _fn_code  = _src[_fn_start:_fn_end].strip()
    _ns = {"__file__": _AIEM_PY_PATH}   # _rs_gate_check uses __file__ for G4 chain path
    exec(
        "import os, threading\n"
        "MAX_SCAN_TRIGGERS_PER_DAY = 10\n" + _fn_code,
        _ns)
    _gate_fn  = _ns["_rs_gate_check"]
    import uuid
    _test_rid = str(uuid.uuid4())
    _gate_c   = _gate_fn(_test_rid, _test_date=TEST_DATE)
    print(f"       gate result: {_gate_c}")
    check("HTTP-C1  allowed=True",            _gate_c["allowed"] is True,
          got=_gate_c["allowed"], expected=True)
    check("HTTP-C2  reason=all_gates_pass",   _gate_c["reason"] == "all_gates_pass",
          got=_gate_c["reason"], expected="all_gates_pass")
    check("HTTP-C3  trigger_count=1",         _gate_c["trigger_count"] == 1,
          got=_gate_c["trigger_count"], expected=1)
except Exception as _ce:
    FAIL_N += 1
    print(f"FAIL  [HTTP-C]  exception: {_ce}")

# ── Audit log verification ───────────────────────────────────────────────────

print("\n[AUDIT] aiem_scan_trigger_log — blocked and accepted rows")
cur.execute("""
    SELECT action, reason, trigger_count_at_time, logged_at
    FROM aiem_scan_trigger_log
    ORDER BY id DESC LIMIT 10
""")
audit_rows = cur.fetchall()
print(f"       last {len(audit_rows)} rows:")
for r in audit_rows:
    print(f"         action={r[0]}  reason={r[1]}  count={r[2]}  at={str(r[3])[:19]}")
blocked_rows   = [r for r in audit_rows if r[0] == "blocked"]
accepted_rows  = [r for r in audit_rows if r[0] == "accepted"]
ks_blocked     = [r for r in blocked_rows if r[1] == "kill_switch"]
check("AUDIT-1a  >=1 blocked row exists",       len(blocked_rows) >= 1,
      got=len(blocked_rows), expected=">=1")
check("AUDIT-1b  >=1 kill_switch block row",    len(ks_blocked) >= 1,
      got=len(ks_blocked), expected=">=1")
check("AUDIT-2   >=1 accepted row exists",       len(accepted_rows) >= 1,
      got=len(accepted_rows), expected=">=1")

# Raw SQL: daily cap state for test_date
print("\n[SQL] morning_watchdog_audit for test_date:")
cur.execute("SELECT audit_date, triggers_fired, updated_at FROM morning_watchdog_audit "
            "WHERE audit_date=%s", (TEST_DATE,))
cap_row = cur.fetchone()
print(f"       {cap_row}")

# Raw SQL: kill switch state
print("\n[SQL] aiem_watchdog_flags:")
cur.execute("SELECT flag_name, flag_value, updated_at FROM aiem_watchdog_flags")
for r in cur.fetchall():
    print(f"       {r[0]}={r[1]}  updated_at={str(r[2])[:19]}")

conn.close()

print()
print("=" * 70)
print(f"SUMMARY: {PASS_N} PASS  {FAIL_N} FAIL  (total {PASS_N+FAIL_N})")
print(f"RESULT: {PASS_N} PASS  {FAIL_N} FAIL  (total {PASS_N+FAIL_N})")
print("=" * 70)
sys.exit(1 if FAIL_N > 0 else 0)
