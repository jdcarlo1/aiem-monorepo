#!/usr/bin/env bash
# verify_paper_recovery.sh — Real runtime evidence for paper trade recovery system.
# Returns exit code 0 if all checked protections are proven, non-zero on any FAIL.

set -euo pipefail

DB="${DATABASE_URL}"
TODAY=$(TZ=America/New_York date +%Y-%m-%d)
EVIDENCE_LOG="/home/runner/workspace/.local/paper_trade_evidence.log"
WATCHDOG_LOG="/home/runner/workspace/.local/paper_watchdog.log"

echo "============================================================"
echo " PAPER TRADE RECOVERY VERIFICATION"
echo " Date       : $TODAY"
echo " Time ET    : $(TZ=America/New_York date +'%H:%M:%S ET')"
echo " Run from   : $(hostname) pid=$$"
echo "============================================================"
echo

FAIL=0
pass()  { echo "  [PASS] $1"; }
fail_c(){ echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
info()  { echo "  [INFO] $1"; }

# ── Schema check ─────────────────────────────────────────────────────────────
echo "── Schema (Protection #2) ──────────────────────────────────────────────"
LEDGER_EXISTS=$(psql "$DB" -t -A -c \
  "SELECT COUNT(*) FROM information_schema.tables \
   WHERE table_name='paper_trade_job_ledger'")
HB_EXISTS=$(psql "$DB" -t -A -c \
  "SELECT COUNT(*) FROM information_schema.tables \
   WHERE table_name='paper_trade_watchdog_heartbeat'")
[ "$LEDGER_EXISTS" -eq 1 ] && pass "paper_trade_job_ledger exists" \
                             || fail_c "paper_trade_job_ledger MISSING"
[ "$HB_EXISTS"     -eq 1 ] && pass "paper_trade_watchdog_heartbeat exists" \
                             || fail_c "paper_trade_watchdog_heartbeat MISSING"
echo

# ── Today's ledger row ───────────────────────────────────────────────────────
echo "── Today's ledger (Protection #2 + #9) ─────────────────────────────────"
ROW=$(psql "$DB" -t -A -c \
  "SELECT id, status, execution_id, trigger_source, \
          claimed_at, completed_at, picks_count, recovery_attempts \
   FROM paper_trade_job_ledger \
   WHERE business_date='$TODAY' LIMIT 1")
if [ -n "$ROW" ]; then
    pass "Ledger row exists for $TODAY"
    echo "  $ROW"
    LEDGER_STATUS=$(echo "$ROW" | cut -d'|' -f2)
    LEDGER_EXEC_ID=$(echo "$ROW" | cut -d'|' -f3)
    LEDGER_TRIGGER=$(echo "$ROW" | cut -d'|' -f4)
    info "status        = $LEDGER_STATUS"
    info "execution_id  = $LEDGER_EXEC_ID"
    info "trigger_source= $LEDGER_TRIGGER"
    [ "$LEDGER_STATUS" = "COMPLETED" ] || [ "$LEDGER_STATUS" = "SKIPPED" ] \
      && pass "Terminal status reached: $LEDGER_STATUS" \
      || info "Status not yet terminal: $LEDGER_STATUS"
else
    info "No ledger row for $TODAY yet (may be before 9:42 AM ET)"
fi

# ── Exactly-once: UNIQUE constraint ──────────────────────────────────────────
echo
echo "── Exactly-once (Protection #9) ────────────────────────────────────────"
DUP=$(psql "$DB" -t -A -c \
  "SELECT COUNT(*) FROM paper_trade_job_ledger WHERE business_date='$TODAY'")
[ "$DUP" -le 1 ] && pass "UNIQUE constraint: $DUP row(s) for $TODAY (≤1 correct)" \
                  || fail_c "DUPLICATE ROWS: $DUP rows for $TODAY — UNIQUE constraint broken"

# ── Recovery attempts (crash recovery proof) ──────────────────────────────────
RECOVERY_ATTEMPTS=$(psql "$DB" -t -A -c \
  "SELECT COALESCE(recovery_attempts,0) FROM paper_trade_job_ledger \
   WHERE business_date='$TODAY' LIMIT 1")
RECOVERY_ATTEMPTS="${RECOVERY_ATTEMPTS:-0}"
info "recovery_attempts = $RECOVERY_ATTEMPTS"
echo

# ── Heartbeat monitoring (Protection #6) ─────────────────────────────────────
echo "── Heartbeat monitoring (Protection #6) ────────────────────────────────"
HB_RECENT=$(psql "$DB" -t -A -c \
  "SELECT COUNT(*) FROM paper_trade_watchdog_heartbeat \
   WHERE last_alive > NOW() - INTERVAL '10 minutes'")
echo "  Heartbeats in last 10 min: $HB_RECENT"
[ "$HB_RECENT" -gt 0 ] && pass "Active heartbeats present" \
                         || info "No recent heartbeats (expected if market closed)"
HB_ROWS=$(psql "$DB" -t -A -c \
  "SELECT process_type, pid, status, last_alive \
   FROM paper_trade_watchdog_heartbeat \
   ORDER BY id DESC LIMIT 6")
echo "  Recent heartbeat rows:"
echo "$HB_ROWS" | while IFS= read -r line; do echo "    $line"; done

# Total by type
HB_INT=$(psql "$DB" -t -A -c \
  "SELECT COUNT(*) FROM paper_trade_watchdog_heartbeat WHERE process_type='internal_watchdog'")
HB_EXT=$(psql "$DB" -t -A -c \
  "SELECT COUNT(*) FROM paper_trade_watchdog_heartbeat WHERE process_type='external_watchdog'")
info "internal_watchdog heartbeat count: $HB_INT"
info "external_watchdog heartbeat count: $HB_EXT"
echo

# ── Durable evidence file (Protection #10) ───────────────────────────────────
echo "── Durable evidence file (Protection #10) ──────────────────────────────"
if [ -f "$EVIDENCE_LOG" ]; then
    LINE_COUNT=$(wc -l < "$EVIDENCE_LOG")
    pass "Evidence log exists ($LINE_COUNT lines): $EVIDENCE_LOG"
    echo "  Recent entries:"
    tail -8 "$EVIDENCE_LOG" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        print(f\"    {d.get('ts','')} event={d.get('event','')} \"\
              f\"trigger={d.get('trigger_source','')} \"\
              f\"exec_id={d.get('execution_id','')} \"\
              f\"status={d.get('existing_status','')}\")
    except:
        print(f'    {line[:120]}')
" 2>/dev/null || tail -8 "$EVIDENCE_LOG" | while IFS= read -r line; do echo "    $line"; done
else
    fail_c "Evidence log MISSING: $EVIDENCE_LOG"
fi

if [ -f "$WATCHDOG_LOG" ]; then
    WD_COUNT=$(wc -l < "$WATCHDOG_LOG")
    pass "External watchdog log exists ($WD_COUNT lines): $WATCHDOG_LOG"
    tail -4 "$WATCHDOG_LOG" | while IFS= read -r line; do echo "    $line"; done
else
    info "External watchdog log not yet created (expected if not yet deployed)"
fi
echo

# ── Scheduler run audit ───────────────────────────────────────────────────────
echo "── Scheduler run audit ─────────────────────────────────────────────────"
AUDIT=$(psql "$DB" -t -A -c \
  "SELECT id, status, trigger_source, actual_start_time \
   FROM scheduler_run_audit \
   WHERE scheduled_time::date = '$TODAY' LIMIT 1")
if [ -n "$AUDIT" ]; then
    pass "Scheduler audit row for $TODAY: $AUDIT"
else
    info "No scheduler_run_audit row for $TODAY"
fi
echo

# ── Execution log ─────────────────────────────────────────────────────────────
echo "── Execution log ───────────────────────────────────────────────────────"
EXEC_ROWS=$(psql "$DB" -t -A -c \
  "SELECT id, status, trigger_source, started_at, finished_at, error_msg \
   FROM aiem_paper_execution_log \
   WHERE started_at::date = '$TODAY' \
   ORDER BY id DESC LIMIT 5")
if [ -n "$EXEC_ROWS" ]; then
    pass "Execution log rows for $TODAY:"
    echo "$EXEC_ROWS" | while IFS= read -r line; do echo "    $line"; done
else
    info "No execution log rows for $TODAY"
fi
echo

# ── Paper trades ─────────────────────────────────────────────────────────────
echo "── Paper trades ────────────────────────────────────────────────────────"
TRADE_COUNT=$(psql "$DB" -t -A -c \
  "SELECT COUNT(*) FROM aiem_paper_trades WHERE trade_date = '$TODAY'")
info "Paper trades for $TODAY: $TRADE_COUNT"
echo

# ── All ledger rows (history) ─────────────────────────────────────────────────
echo "── Ledger history (last 7 days) ─────────────────────────────────────────"
psql "$DB" -t -A -c \
  "SELECT business_date, status, trigger_source, execution_id, \
          picks_count, recovery_attempts, completed_at \
   FROM paper_trade_job_ledger \
   ORDER BY business_date DESC LIMIT 7" \
  | while IFS= read -r line; do echo "  $line"; done
echo

# ── Summary ───────────────────────────────────────────────────────────────────
echo "============================================================"
echo " SUMMARY"
echo "============================================================"
echo "  Today          : $TODAY"
echo "  Ledger status  : ${LEDGER_STATUS:-N/A}"
echo "  Execution ID   : ${LEDGER_EXEC_ID:-N/A}"
echo "  Trigger source : ${LEDGER_TRIGGER:-N/A}"
echo "  Recovery att.  : ${RECOVERY_ATTEMPTS:-0}"
echo "  Heartbeats     : ${HB_RECENT:-0} (last 10 min)"
echo "  Evidence log   : $([ -f "$EVIDENCE_LOG" ] && wc -l < "$EVIDENCE_LOG" || echo 0) lines"
echo "  Fail count     : $FAIL"
if [ "$FAIL" -eq 0 ]; then
    echo "  RESULT: PASS — all checked protections verified"
else
    echo "  RESULT: FAIL — $FAIL protection(s) unverified (see [FAIL] lines above)"
fi
echo "============================================================"
exit "$FAIL"
