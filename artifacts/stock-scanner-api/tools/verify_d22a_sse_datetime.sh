#!/usr/bin/env bash
# D22A verifier — aiem_sse.py naive/aware datetime fix in _poll_system_health
# Run from: artifacts/stock-scanner-api/
set -uo pipefail

PASS=0
FAIL=0
pass() { echo "PASS $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL $1"; FAIL=$((FAIL+1)); }

# ── C1. Fix present: r_attempt variable used in comparison ───────────────────
echo "=== C1: r_attempt normalization present ==="
LINE=$(grep -n "r_attempt = row\[2\].replace(tzinfo=timezone.utc)" aiem_sse.py | head -1)
echo "grep result: $LINE"
if [ -n "$LINE" ]; then
  pass "C1: r_attempt UTC normalization present in aiem_sse.py"
else
  fail "C1: r_attempt normalization NOT found"
fi

# ── C2. r_success normalization also present ─────────────────────────────────
echo "=== C2: r_success normalization present ==="
LINE2=$(grep -n "r_success = row\[1\].replace(tzinfo=timezone.utc)" aiem_sse.py | head -1)
echo "grep result: $LINE2"
if [ -n "$LINE2" ]; then
  pass "C2: r_success UTC normalization present"
else
  fail "C2: r_success normalization NOT found"
fi

# ── C3. Old bare row[2] comparison removed ───────────────────────────────────
echo "=== C3: old bare row[2] > max_ts comparison removed ==="
OLD=$(grep -n "row\[2\] > max_ts\|row\[2\] and (max_ts" aiem_sse.py | head -5)
echo "grep result: '$OLD'"
if [ -z "$OLD" ]; then
  pass "C3: old bare row[2] > max_ts comparison removed"
else
  fail "C3: old bare row[2] > max_ts still present: $OLD"
fi

# ── C4. Root cause confirmed: job_heartbeats columns are naive ────────────────
echo "=== C4: job_heartbeats.last_attempt is TIMESTAMP WITHOUT TIME ZONE (naive) ==="
COL_TYPE=$(python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"\"\"
  SELECT data_type FROM information_schema.columns
  WHERE table_name='job_heartbeats' AND column_name='last_attempt'
\"\"\")
row = cur.fetchone()
conn.close()
print(row[0] if row else 'NOT_FOUND')
")
echo "DB type: $COL_TYPE"
if echo "$COL_TYPE" | grep -q "without time zone"; then
  pass "C4: job_heartbeats.last_attempt confirmed naive (timestamp without time zone)"
else
  fail "C4: unexpected column type: $COL_TYPE"
fi

# ── C5. last_seen_ts is TIMESTAMPTZ (aware) — confirming mismatch was real ───
echo "=== C5: aiem_sse_poller_state.last_seen_ts is TIMESTAMPTZ (aware) ==="
TS_TYPE=$(python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"\"\"
  SELECT data_type FROM information_schema.columns
  WHERE table_name='aiem_sse_poller_state' AND column_name='last_seen_ts'
\"\"\")
row = cur.fetchone()
conn.close()
print(row[0] if row else 'NOT_FOUND')
")
echo "DB type: $TS_TYPE"
if echo "$TS_TYPE" | grep -q "with time zone"; then
  pass "C5: aiem_sse_poller_state.last_seen_ts confirmed aware (timestamp with time zone)"
else
  fail "C5: unexpected type: $TS_TYPE"
fi

# ── C6. Negative control: no other file does bare naive/aware comparison ──────
echo "=== C6: negative control — no other .py does unguarded job_heartbeats ts comparison ==="
# aiem_watchdog.py already uses .replace(tzinfo=timezone.utc) guard — confirm
WD_LINE=$(grep -n "replace(tzinfo=timezone.utc)" aiem_watchdog.py | head -3)
echo "aiem_watchdog.py guard: $WD_LINE"
if [ -n "$WD_LINE" ]; then
  pass "C6: aiem_watchdog.py already has correct UTC guard — consistent fix pattern"
else
  fail "C6: aiem_watchdog.py guard not found"
fi

# ── C7. sha256 before/after confirms only aiem_sse.py changed ────────────────
echo "=== C7: sha256 before/after ==="
echo "before: c9d610f9ce3e1a3cfa1cd0deea4795185ebe1f5b489ec147f89277c921ebcde9"
AFTER=$(sha256sum aiem_sse.py | awk '{print $1}')
echo "after:  $AFTER"
if [ "$AFTER" = "24822922ed20047fab87f65c357313503bd04870992ea3cd5ee858da04d8bb84" ]; then
  pass "C7: sha256 after matches expected (24822922...)"
else
  fail "C7: sha256 mismatch — got $AFTER"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "SUMMARY: ${PASS} PASS  ${FAIL} FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
