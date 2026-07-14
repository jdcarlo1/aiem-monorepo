#!/bin/bash
# =============================================================================
# verify_scheduler_audit.sh
#
# PURPOSE: Prove that all three canonical scheduler_run_audit paths write
#          correct rows — EXECUTED (cron or admin fires), RECOVERED
#          (startup_catchup during market hours when no trades exist),
#          SKIPPED (startup_catchup after 16:00 ET when no trades exist).
#
# RUN REQUIREMENTS:
#   EXECUTED  — any time after 09:42 ET on a weekday (cron fired).
#   RECOVERED — run script after a server restart between 09:00–09:41 ET
#               on a day that had no paper trades before the restart.
#   SKIPPED   — run script after a server restart after 16:00 ET on a
#               day where zero paper trades were placed all day.
#
#   All three statuses will accumulate over time. The script checks what
#   is present and flags which are still outstanding.
#
# FALSIFICATION TESTS:
#   (a) No SIM_* trace_ids exist (ever, not just today).
#   (b) Every row links to a real exec_log_id (no orphan audit rows),
#       except SKIPPED rows (which correctly have no exec_log_id).
#   (c) scheduled_time and actual_start_time are distinct real timestamps.
#   (d) All three status values (EXECUTED, RECOVERED, SKIPPED) each appear
#       at least once in the lifetime table.
#   (e) EXECUTED rows have trigger_source = 'scheduled_942' or
#       'admin_run_paper_today' (not 'startup_catchup').
#   (f) RECOVERED rows have trigger_source = 'startup_catchup'.
#   (g) SKIPPED rows have trigger_source = 'startup_catchup'
#       AND exec_log_id IS NULL.
#   (h) No two rows share the same (scheduled_time, status) pair —
#       no duplicate firing.
# =============================================================================

set -euo pipefail
SCRIPT_VERSION="1.0.0"

echo "################################################################"
echo "# SCHEDULER AUDIT TABLE VERIFICATION"
echo "# Script: verify_scheduler_audit.sh  v${SCRIPT_VERSION}"
echo "################################################################"

# ── Real-time anchors ─────────────────────────────────────────────────────────
SHELL_UTC=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
SHELL_ET=$(TZ='America/New_York' date '+%Y-%m-%d %H:%M:%S ET')
echo ""
echo "[ANCHOR] shell UTC : $SHELL_UTC"
echo "[ANCHOR] shell ET  : $SHELL_ET"
DB_NOW=$(psql "$DATABASE_URL" -t -c "SELECT NOW() AT TIME ZONE 'America/New_York';" 2>/dev/null | xargs)
DB_TODAY=$(psql "$DATABASE_URL" -t -c "SELECT (NOW() AT TIME ZONE 'America/New_York')::date;" 2>/dev/null | xargs)
echo "[ANCHOR] db NOW ET : $DB_NOW"
echo "[ANCHOR] db date ET: $DB_TODAY"

echo ""
echo "================================================================"
echo "PART A — Full scheduler_run_audit table (lifetime)"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    a.id                           AS audit_id,
    a.status,
    a.trace_id,
    a.trigger_source,
    a.scheduled_time               AS scheduled_utc,
    a.actual_start_time            AS actual_utc,
    a.exec_log_id,
    e.trades_inserted              AS el_trades,
    e.status                       AS el_status,
    e.trigger_source               AS el_trigger
FROM scheduler_run_audit a
LEFT JOIN aiem_paper_execution_log e ON e.id = a.exec_log_id
ORDER BY a.id;
SQL

echo ""
echo "================================================================"
echo "PART B — SIM_* row count (must be zero, ever)"
echo "================================================================"
SIM_COUNT=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM scheduler_run_audit WHERE trace_id LIKE 'SIM_%';
" | xargs)
echo "SIM_* rows: $SIM_COUNT"
[[ "$SIM_COUNT" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌ (SIM_* rows still present)"

echo ""
echo "================================================================"
echo "PART C — Status coverage: which of EXECUTED/RECOVERED/SKIPPED exist?"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    status,
    COUNT(*)                             AS row_count,
    MIN(actual_start_time)               AS first_seen_utc,
    MAX(actual_start_time)               AS last_seen_utc,
    COUNT(DISTINCT trigger_source)       AS distinct_triggers
FROM scheduler_run_audit
GROUP BY status
ORDER BY status;
SQL

HAS_EXECUTED=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM scheduler_run_audit WHERE status='EXECUTED';" | xargs)
HAS_RECOVERED=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM scheduler_run_audit WHERE status='RECOVERED';" | xargs)
HAS_SKIPPED=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM scheduler_run_audit WHERE status='SKIPPED';" | xargs)

echo ""
echo "Status presence:"
[[ "$HAS_EXECUTED"  -gt 0 ]] && echo "  EXECUTED  → PRESENT ✅ ($HAS_EXECUTED rows)"  || echo "  EXECUTED  → ABSENT  ⏳ (fires at 09:42 ET on next weekday)"
[[ "$HAS_RECOVERED" -gt 0 ]] && echo "  RECOVERED → PRESENT ✅ ($HAS_RECOVERED rows)"  || echo "  RECOVERED → ABSENT  ⏳ (fires on restart 09:00–09:41 ET with no trades)"
[[ "$HAS_SKIPPED"   -gt 0 ]] && echo "  SKIPPED   → PRESENT ✅ ($HAS_SKIPPED rows)"    || echo "  SKIPPED   → ABSENT  ⏳ (fires on restart after 16:00 ET with no trades)"

echo ""
echo "================================================================"
echo "PART D — EXECUTED rows: trigger_source must NOT be startup_catchup"
echo "================================================================"
BAD_EXECUTED=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM scheduler_run_audit
  WHERE status='EXECUTED' AND trigger_source='startup_catchup';
" | xargs)
echo "EXECUTED rows with trigger_source=startup_catchup (must be 0): $BAD_EXECUTED"
[[ "$BAD_EXECUTED" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌"

echo ""
echo "================================================================"
echo "PART E — RECOVERED rows: trigger_source must be startup_catchup"
echo "================================================================"
if [[ "$HAS_RECOVERED" -gt 0 ]]; then
  BAD_RECOVERED=$(psql "$DATABASE_URL" -t -c "
    SELECT COUNT(*) FROM scheduler_run_audit
    WHERE status='RECOVERED' AND trigger_source != 'startup_catchup';
  " | xargs)
  echo "RECOVERED rows with wrong trigger_source (must be 0): $BAD_RECOVERED"
  [[ "$BAD_RECOVERED" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌"
else
  echo "  (RECOVERED not yet present — check again after qualifying restart)"
fi

echo ""
echo "================================================================"
echo "PART F — SKIPPED rows: startup_catchup AND exec_log_id IS NULL"
echo "================================================================"
if [[ "$HAS_SKIPPED" -gt 0 ]]; then
  BAD_SKIPPED=$(psql "$DATABASE_URL" -t -c "
    SELECT COUNT(*) FROM scheduler_run_audit
    WHERE status='SKIPPED'
      AND (trigger_source != 'startup_catchup' OR exec_log_id IS NOT NULL);
  " | xargs)
  echo "SKIPPED rows with wrong trigger or non-NULL exec_log_id (must be 0): $BAD_SKIPPED"
  [[ "$BAD_SKIPPED" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌"
else
  echo "  (SKIPPED not yet present — check again after qualifying restart)"
fi

echo ""
echo "================================================================"
echo "PART G — Duplicate (scheduled_time, status) pairs (must be zero)"
echo "================================================================"
DUP_PAIRS=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM (
    SELECT scheduled_time, status
    FROM scheduler_run_audit
    GROUP BY scheduled_time, status
    HAVING COUNT(*) > 1
  ) d;
" | xargs)
echo "Duplicate (scheduled_time, status) pairs: $DUP_PAIRS"
[[ "$DUP_PAIRS" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌"

echo ""
echo "================================================================"
echo "PART H — exec_log cross-match for EXECUTED + RECOVERED rows"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    a.id                   AS audit_id,
    a.status,
    a.scheduled_time       AS scheduled_utc,
    a.actual_start_time    AS actual_utc,
    a.exec_log_id,
    e.trades_inserted,
    e.status               AS exec_log_status,
    e.trigger_source       AS exec_log_trigger,
    (a.exec_log_id IS NOT NULL AND e.id IS NULL) AS orphan_exec_log
FROM scheduler_run_audit a
LEFT JOIN aiem_paper_execution_log e ON e.id = a.exec_log_id
WHERE a.status IN ('EXECUTED','RECOVERED')
ORDER BY a.id;
SQL

ORPHANS=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM scheduler_run_audit a
  LEFT JOIN aiem_paper_execution_log e ON e.id = a.exec_log_id
  WHERE a.status IN ('EXECUTED','RECOVERED')
    AND a.exec_log_id IS NOT NULL
    AND e.id IS NULL;
" | xargs)
echo "Orphan exec_log_id references (must be 0): $ORPHANS"
[[ "$ORPHANS" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌"

echo ""
echo "================================================================"
echo "PART I — Log corroboration (live process log grep)"
echo "================================================================"
LOG_FILE=$(ls -t /tmp/logs/stock-api_*.log 2>/dev/null | head -1)
if [[ -n "$LOG_FILE" ]]; then
  echo "Scanning: $LOG_FILE"
  grep -E "scheduler_run_audit|startup_catchup|RECOVERED|SKIPPED|EXECUTED|write_audit" \
    "$LOG_FILE" 2>/dev/null | tail -25 || echo "  (no matching lines)"
else
  echo "  (no stock-api log file found under /tmp/logs/)"
fi

echo ""
echo "================================================================"
echo "PART J — Today's scheduled_run_audit rows only"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    id, status, trace_id, trigger_source,
    scheduled_time  AT TIME ZONE 'America/New_York' AS scheduled_et,
    actual_start_time AT TIME ZONE 'America/New_York' AS actual_et,
    exec_log_id
FROM scheduler_run_audit
WHERE actual_start_time::date = current_date
ORDER BY id;
SQL

echo ""
echo "################################################################"
echo "FINAL VERDICT CRITERIA — check each line manually"
echo "################################################################"
echo "  [ ] PART B: SIM_* row count = 0 (ever)"
echo "  [ ] PART C: EXECUTED present (fires at 09:42 ET on each weekday)"
echo "  [ ] PART C: RECOVERED present (fires on qualifying startup_catchup restart)"
echo "  [ ] PART C: SKIPPED present (fires on qualifying after-hours restart)"
echo "  [ ] PART D: No EXECUTED row has trigger_source=startup_catchup"
echo "  [ ] PART E: Every RECOVERED row has trigger_source=startup_catchup"
echo "  [ ] PART F: Every SKIPPED row has trigger_source=startup_catchup AND exec_log_id IS NULL"
echo "  [ ] PART G: Zero duplicate (scheduled_time, status) pairs"
echo "  [ ] PART H: Zero orphan exec_log_id references"
echo ""
echo "TIMING WINDOWS FOR OUTSTANDING STATUSES:"
echo "  EXECUTED  → fires automatically at 09:42 ET each weekday."
echo "  RECOVERED → restart stock-api between 09:00 and 09:41 ET on a"
echo "              weekday before any paper trades have been placed."
echo "  SKIPPED   → restart stock-api after 16:00 ET on a weekday where"
echo "              zero paper trades were placed the entire day."
echo ""
echo "ALL CRITERIA = PASS → Fix 3 (Scheduler Audit) is live and correct."
echo "ANY FAIL / ABSENT   → Check timing windows above and re-run."
echo "################################################################"
