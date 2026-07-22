#!/usr/bin/env bash
# D24 verifier — aiem_morning_scan STALE watchdog fix
# Seals under verified_run.sh (flock + SHA-256 + SEQ).
# Run from: artifacts/stock-scanner-api/  (verified_run.sh cd's there)
# Checks:
#   C1. Startup-catchup block present in main.py
#   C2. Recovery window 9:07-12:00 ET condition present
#   C3. record_job_success wired in catchup block
#   C4. job_heartbeats has today's aiem_morning_scan success
#   C5. Watchdog staleness: elapsed < 26h
#   C6. Negative control: _JOB_STALENESS_HOURS has >= 3 jobs
#   C7. CronTrigger timezone=_ET present (no UTC bug)
#   C8. check_job_health uses utcnow() (no tz mismatch)
set -uo pipefail

PASS=0
FAIL=0
pass() { echo "PASS $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL $1"; FAIL=$((FAIL+1)); }

# ── C1. Startup catchup block present in main.py ─────────────────────────────
echo "=== C1: startup catchup block in main.py ==="
CATCHUP_LINE=$(grep -n "aiem_morning_scan missed for" main.py | head -1)
echo "grep result: $CATCHUP_LINE"
if [ -n "$CATCHUP_LINE" ]; then
  pass "C1: startup_catchup contains aiem_morning_scan catch-up block"
else
  fail "C1: startup_catchup block NOT found in main.py"
fi

# ── C2. Recovery window logic present (9:07–12:00 ET) ───────────────────────
echo "=== C2: recovery window condition in catchup ==="
WINDOW_LINE=$(grep -n "9 \* 60 + 7 <= _hour_min_et < 12 \* 60" main.py | head -1)
echo "grep result: $WINDOW_LINE"
if [ -n "$WINDOW_LINE" ]; then
  pass "C2: recovery window 9:07-12:00 ET condition present"
else
  fail "C2: recovery window condition NOT found"
fi

# ── C3. record_job_success called in catchup path ────────────────────────────
echo "=== C3: record_job_success wired in catchup ==="
RJOB_LINE=$(grep -n 'record_job_success("aiem_morning_scan")' main.py | head -5)
echo "grep result: $RJOB_LINE"
if echo "$RJOB_LINE" | grep -q "78[12][0-9]"; then
  pass "C3: record_job_success(aiem_morning_scan) wired in catchup block"
else
  fail "C3: record_job_success not found in catchup block range"
fi

# ── C4. job_heartbeats: today's aiem_morning_scan success ────────────────────
echo "=== C4: job_heartbeats — today's morning scan success ==="
TODAY_OUT=$(python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"\"\"
  SELECT last_success, consecutive_failures
  FROM job_heartbeats
  WHERE job_name='aiem_morning_scan'
\"\"\")
row = cur.fetchone()
conn.close()
if row:
    print(f'last_success={row[0]}  consec_failures={row[1]}')
else:
    print('NO_ROW')
")
echo "DB result: $TODAY_OUT"
if echo "$TODAY_OUT" | grep -q "2026-07-22"; then
  pass "C4: job_heartbeats shows today (2026-07-22) success for aiem_morning_scan"
else
  fail "C4: job_heartbeats does NOT show today's success"
fi

# ── C5. Watchdog staleness math: elapsed < 26h ──────────────────────────────
echo "=== C5: watchdog staleness — elapsed < 26h ==="
ELAPSED_H=$(python3 -c "
import psycopg2, os
from datetime import datetime as dt
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT last_success FROM job_heartbeats WHERE job_name='aiem_morning_scan'\")
row = cur.fetchone()
conn.close()
if row and row[0]:
    elapsed = (dt.utcnow() - row[0]).total_seconds() / 3600
    print(f'{elapsed:.2f}')
else:
    print('999')
")
echo "elapsed hours since last_success: ${ELAPSED_H}h"
ELAPSED_INT=$(python3 -c "print(int(float('$ELAPSED_H')))")
if [ "$ELAPSED_INT" -lt 26 ]; then
  pass "C5: elapsed ${ELAPSED_H}h < 26h — watchdog will NOT flag STALE"
else
  fail "C5: elapsed ${ELAPSED_H}h >= 26h — watchdog STILL flags STALE"
fi

# ── C6. Negative control: _JOB_STALENESS_HOURS entries ─────────────────────
echo "=== C6: negative control — _JOB_STALENESS_HOURS ==="
JOB_COUNT=$(grep -A 30 "_JOB_STALENESS_HOURS = {" main.py | \
  grep '".*":' | wc -l | tr -d ' ')
echo "Monitored job count: $JOB_COUNT"
if [ "$JOB_COUNT" -ge 3 ]; then
  pass "C6: found $JOB_COUNT monitored jobs in _JOB_STALENESS_HOURS"
else
  fail "C6: expected >=3 jobs in _JOB_STALENESS_HOURS, got $JOB_COUNT"
fi

# ── C7. CronTrigger timezone=_ET present (no UTC bug) ───────────────────────
echo "=== C7: CronTrigger timezone=_ET present ==="
CRON_LINE=$(grep -n "hour=9, minute=7, timezone=_ET\|hour=9.*minute=7.*timezone=_ET" \
  main.py | head -3)
echo "grep result: $CRON_LINE"
if [ -n "$CRON_LINE" ]; then
  pass "C7: aiem_morning_scan CronTrigger has timezone=_ET (no UTC bug)"
else
  fail "C7: CronTrigger timezone=_ET NOT found — may have UTC bug"
fi

# ── C8. check_job_health uses utcnow() ──────────────────────────────────────
echo "=== C8: watchdog uses utcnow() ==="
UTCNOW_LINE=$(grep -n "utcnow\(\)" main.py | \
  awk -F: '$1>=4200 && $1<=4270' | head -3)
echo "grep result: $UTCNOW_LINE"
if [ -n "$UTCNOW_LINE" ]; then
  pass "C8: check_job_health uses utcnow() — no tz mismatch in STALE calc"
else
  fail "C8: utcnow() not found in check_job_health range"
fi

# ── Summary (PSV8 requires exactly this format) ──────────────────────────────
echo ""
echo "SUMMARY: ${PASS} PASS  ${FAIL} FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
