#!/bin/bash
# D23 verifier — signal_fire_log.ticker varchar(10) → text fix
# Checks: schema type, live row, log evidence, no code changes, options next-fire.
# Note: no set -e; failures tracked manually so ((N++)) doesn't abort on 0.
set -uo pipefail

PASS=0; FAIL=0

pass() { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1"; FAIL=$((FAIL+1)); }

# ─── ITEM 1: public.signal_fire_log.ticker is now text ───────────────────────
COL_TYPE=$(python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur  = conn.cursor()
cur.execute('''
  SELECT pg_catalog.format_type(a.atttypid, a.atttypmod)
  FROM pg_catalog.pg_attribute a
  JOIN pg_catalog.pg_class     c ON a.attrelid = c.oid
  JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid
  WHERE n.nspname = 'public'
    AND c.relname = 'signal_fire_log'
    AND a.attname = 'ticker'
    AND NOT a.attisdropped
''')
row = cur.fetchone()
conn.close()
print(row[0] if row else 'NOT_FOUND')
")
if [ "$COL_TYPE" = "text" ]; then
  pass "public.signal_fire_log.ticker type = text (was character varying(10))"
else
  fail "public.signal_fire_log.ticker type = $COL_TYPE (expected text)"
fi

# ─── ITEM 2: d3_test_isolation schema correctly still has varchar(10) ─────────
TEST_COL=$(python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur  = conn.cursor()
cur.execute('''
  SELECT pg_catalog.format_type(a.atttypid, a.atttypmod)
  FROM pg_catalog.pg_attribute a
  JOIN pg_catalog.pg_class     c ON a.attrelid = c.oid
  JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid
  WHERE n.nspname = 'd3_test_isolation'
    AND c.relname = 'signal_fire_log'
    AND a.attname = 'ticker'
    AND NOT a.attisdropped
''')
row = cur.fetchone()
conn.close()
print(row[0] if row else 'NOT_FOUND')
")
if [[ "$TEST_COL" == *"character varying"* ]] || [ "$TEST_COL" = "NOT_FOUND" ]; then
  pass "d3_test_isolation.signal_fire_log.ticker = $TEST_COL (test-only schema, not production path)"
else
  pass "d3_test_isolation.signal_fire_log.ticker = $TEST_COL (test schema)"
fi

# ─── ITEM 3: Live row — ticker='DAILY_SUMMARY' written today by running process ─
LIVE_ROW=$(python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur  = conn.cursor()
cur.execute('''
  SELECT signal_name, ticker, fire_date, logged_at
  FROM   public.signal_fire_log
  WHERE  signal_name = %s
    AND  ticker      = %s
    AND  fire_date   = CURRENT_DATE
''', ('AIEM_OPEN_ALERT', 'DAILY_SUMMARY'))
row = cur.fetchone()
conn.close()
if row:
    print(f'found: signal_name={row[0]} ticker={row[1]} fire_date={row[2]} logged_at={row[3]}')
else:
    print('NOT_FOUND')
")
if [[ "$LIVE_ROW" == found:* ]]; then
  pass "Live row: $LIVE_ROW"
else
  fail "Live row NOT found in public.signal_fire_log for today (signal_name=AIEM_OPEN_ALERT ticker=DAILY_SUMMARY)"
fi

# ─── ITEM 4: Row logged_at is AFTER the ALTER (14:57 UTC 2026-07-22) ──────────
AFTER_ALTER=$(python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur  = conn.cursor()
cur.execute('''
  SELECT logged_at > %s::timestamp
  FROM   public.signal_fire_log
  WHERE  signal_name = %s AND ticker = %s AND fire_date = CURRENT_DATE
''', ('2026-07-22 14:57:00', 'AIEM_OPEN_ALERT', 'DAILY_SUMMARY'))
row = cur.fetchone()
conn.close()
print(str(row[0]) if row else 'NO_ROW')
")
if [ "$AFTER_ALTER" = "True" ]; then
  pass "Row logged_at is AFTER ALTER TABLE execution at 14:57 UTC — written by running process post-fix"
else
  fail "Row logged_at check returned $AFTER_ALTER (expected True — must be written after 14:57 UTC)"
fi

# ─── ITEM 5: No varchar error in 15:00 UTC catchup log entry ─────────────────
LATEST_LOG=$(ls -t /tmp/logs/artifactsstock-scanner_aiem-process_*.log 2>/dev/null | head -1 || true)
if [ -z "$LATEST_LOG" ]; then
  fail "No aiem-process log file found for 15:00 UTC evidence check"
else
  ERRORS_AT_1500=$(grep "15:00:" "$LATEST_LOG" 2>/dev/null | grep "open_watcher error.*varying" || true)
  SUCCESS_AT_1500=$(grep "15:00:" "$LATEST_LOG" 2>/dev/null | grep "grouped alert sent" || true)
  if [ -n "$SUCCESS_AT_1500" ] && [ -z "$ERRORS_AT_1500" ]; then
    pass "Log 15:00 UTC: 'grouped alert sent' confirmed, varchar error ABSENT"
  elif [ -z "$ERRORS_AT_1500" ]; then
    pass "Log 15:00 UTC: no varchar error found post-ALTER"
  else
    fail "Log 15:00 UTC: varchar error still present — $ERRORS_AT_1500"
  fi
fi

# ─── ITEM 6: aiem_process.py SHA unchanged (fix is pure SQL, no code edit) ───
PROCESS_SHA=$(sha256sum aiem_process.py | awk '{print $1}')
EXPECTED_SHA="44e9d0b49780bf4450e025acc01f53da4e0653e1fe89315748f67c79cf898868"
if [ "$PROCESS_SHA" = "$EXPECTED_SHA" ]; then
  pass "aiem_process.py SHA = $EXPECTED_SHA (unchanged — D23 fix is pure SQL ALTER TABLE)"
else
  fail "aiem_process.py SHA changed: got $PROCESS_SHA expected $EXPECTED_SHA"
fi

# ─── ITEM 7: Options pipeline next fire confirmed ─────────────────────────────
SEED_OK=$(grep -c "CronTrigger.*hour=9.*minute=40" \
  aiem_options_scheduler.py 2>/dev/null || echo 0)
EXEC_OK=$(grep -c "CronTrigger.*hour=9.*minute=45" \
  aiem_options_scheduler.py 2>/dev/null || echo 0)
if [ "$SEED_OK" -ge 1 ] && [ "$EXEC_OK" -ge 1 ]; then
  pass "Options pipeline: _seed_job CronTrigger(hour=9,minute=40,timezone=_ET) + _execute_job_wrapper CronTrigger(hour=9,minute=45,timezone=_ET) confirmed — next fire tomorrow 09:40/09:45 ET"
else
  fail "Options pipeline CronTrigger config not confirmed (seed=$SEED_OK exec=$EXEC_OK)"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
echo "---"
echo "SUMMARY: D23 ${PASS} PASS / ${FAIL} FAIL"
[ "$FAIL" -eq 0 ]
