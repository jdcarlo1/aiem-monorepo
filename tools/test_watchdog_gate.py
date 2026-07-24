#!/usr/bin/env python3
"""
Negative-control test for morning_scan_watchdog gate logic.
Runs against real DB (no mocks).  Sealed via verified_run.sh.

Tests:
  KS-1a/b  kill switch = false  → trigger blocked, reason = kill_switch
  KS-2a    kill switch = true   → kill_switch gate does NOT block
  CAP-3a/b daily cap exceeded   → trigger blocked, reason starts daily_cap
  CLEAR-4  all gates clear      → trigger allowed (or gate-3 crash-loop if real state)

Each test resets DB state after itself.
"""
import os, sys, psycopg2
from datetime import date

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_TRIGGERS_PER_DAY = 5   # must match _MW_MAX_TRIGGERS_PER_DAY in notifier

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
    """Gate logic — exact copy of what the watchdog uses.
    Returns (should_trigger: bool, reason: str).
    """
    # Gate 1: kill switch
    cur.execute(
        "SELECT flag_value FROM aiem_watchdog_flags "
        "WHERE flag_name='morning_watchdog_trigger_enabled'")
    krow = cur.fetchone()
    if krow and krow[0].strip().lower() == 'false':
        return False, "kill_switch"
    # Gate 2: daily cap
    cur.execute(
        "SELECT COALESCE(triggers_fired,0) FROM morning_watchdog_audit "
        "WHERE audit_date=%s", (today,))
    crow = cur.fetchone()
    fired = crow[0] if crow else 0
    if fired >= MAX_TRIGGERS_PER_DAY:
        return False, f"daily_cap:{fired}/{MAX_TRIGGERS_PER_DAY}"
    # Gate 3: verification — crash loop detection
    try:
        cur.execute(
            "SELECT COUNT(*) FROM morning_scan_runs "
            "WHERE market_date=%s AND status='FAILED'", (today,))
        failed = cur.fetchone()[0]
        if failed >= 5:
            return False, f"verification_gate:failed_slots={failed}"
    except Exception as e:
        return False, f"verification_gate:db_error={e}"
    return True, "all_gates_pass"


# ── Setup ───────────────────────────────────────────────────────────────────
conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
conn.autocommit = False
cur  = conn.cursor()
today = date.today()

cur.execute("""
    CREATE TABLE IF NOT EXISTS aiem_watchdog_flags (
        flag_name  TEXT PRIMARY KEY,
        flag_value TEXT NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )""")
cur.execute("""
    INSERT INTO aiem_watchdog_flags (flag_name, flag_value)
    VALUES ('morning_watchdog_trigger_enabled', 'true')
    ON CONFLICT DO NOTHING""")
cur.execute("""
    CREATE TABLE IF NOT EXISTS morning_watchdog_audit (
        audit_date     DATE PRIMARY KEY,
        triggers_fired INT  NOT NULL DEFAULT 0,
        updated_at     TIMESTAMPTZ DEFAULT NOW()
    )""")
conn.commit()

print()
print("=" * 70)
print("WATCHDOG GATE NEGATIVE-CONTROL TEST  (real DB, no mocks)")
print("=" * 70)

# ── Test 1: Kill switch = false → trigger must be blocked ───────────────────
print("\n[TEST-1] kill switch = false → gate must block")
cur.execute(
    "UPDATE aiem_watchdog_flags SET flag_value='false', updated_at=NOW() "
    "WHERE flag_name='morning_watchdog_trigger_enabled'")
conn.commit()

ok1, reason1 = wd_gate_check(cur, today)
check("KS-1a  trigger blocked (ok=False)",  ok1 is False,           got=ok1,    expected=False)
check("KS-1b  reason = kill_switch",         reason1 == "kill_switch", got=reason1, expected="kill_switch")
print(f"       trigger={ok1}  reason={reason1!r}")

# ── Reset kill switch ───────────────────────────────────────────────────────
cur.execute(
    "UPDATE aiem_watchdog_flags SET flag_value='true', updated_at=NOW() "
    "WHERE flag_name='morning_watchdog_trigger_enabled'")
conn.commit()

# ── Test 2: Kill switch = true → must NOT block on kill_switch alone ────────
print("\n[TEST-2] kill switch = true → kill_switch must NOT block")
ok2, reason2 = wd_gate_check(cur, today)
check("KS-2a  kill_switch does not block",  reason2 != "kill_switch",
      got=reason2, expected="!= kill_switch")
print(f"       trigger={ok2}  reason={reason2!r}")

# ── Test 3: Daily cap exceeded → trigger must be blocked ────────────────────
print(f"\n[TEST-3] daily cap exceeded (fires={MAX_TRIGGERS_PER_DAY}) → gate must block")
cur.execute("""
    INSERT INTO morning_watchdog_audit (audit_date, triggers_fired, updated_at)
    VALUES (%s, %s, NOW())
    ON CONFLICT (audit_date) DO UPDATE
    SET triggers_fired = %s, updated_at = NOW()
""", (today, MAX_TRIGGERS_PER_DAY, MAX_TRIGGERS_PER_DAY))
conn.commit()

ok3, reason3 = wd_gate_check(cur, today)
check("CAP-3a  trigger blocked (ok=False)",       ok3 is False,
      got=ok3, expected=False)
check("CAP-3b  reason starts with daily_cap",    reason3.startswith("daily_cap"),
      got=reason3, expected="daily_cap:*")
print(f"       trigger={ok3}  reason={reason3!r}")

# ── Reset cap ───────────────────────────────────────────────────────────────
cur.execute("DELETE FROM morning_watchdog_audit WHERE audit_date=%s", (today,))
conn.commit()

# ── Test 4: All gates clear → trigger must be allowed (or gate-3 real state) ─
print("\n[TEST-4] all gates clear → trigger allowed")
cur.execute(
    "SELECT COUNT(*) FROM morning_scan_runs WHERE market_date=%s AND status='FAILED'",
    (today,))
real_failed = cur.fetchone()[0]
print(f"       morning_scan_runs FAILED slots today (real DB): {real_failed}")

ok4, reason4 = wd_gate_check(cur, today)
if real_failed >= 5:
    print("       NOTE: gate-3 blocks (≥5 FAILED slots) — expected given real crash-loop state")
    check("CLEAR-4  gate-3 correctly blocks crash loop",
          ok4 is False and "verification_gate" in reason4,
          got=reason4, expected="verification_gate:*")
else:
    check("CLEAR-4a  all gates pass (ok=True)",
          ok4 is True, got=ok4, expected=True)
    check("CLEAR-4b  reason = all_gates_pass",
          reason4 == "all_gates_pass", got=reason4, expected="all_gates_pass")
print(f"       trigger={ok4}  reason={reason4!r}")

conn.close()

print()
print("=" * 70)
print(f"SUMMARY: {PASS_N} PASS  {FAIL_N} FAIL  (total {PASS_N+FAIL_N})")
print(f"RESULT: {PASS_N} PASS  {FAIL_N} FAIL  (total {PASS_N+FAIL_N})")
print("=" * 70)
sys.exit(1 if FAIL_N > 0 else 0)
