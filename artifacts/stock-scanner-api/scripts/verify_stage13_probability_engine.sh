#!/bin/bash
# =============================================================================
# verify_stage13_probability_engine.sh
#
# PURPOSE: Prove that Diagram-2 Stage 13 (Probability Engine) records PASS
#          with status=SKIP — not FAIL — when ai_short_calls_log and/or
#          polygon_market_daily have no row for the ticker.
#
# RUN AFTER: 09:45 AM ET on any weekday following a real paper-trade run.
# Paste the FULL raw output back, unedited.
#
# FALSIFICATION TESTS:
#   (a) Stage 13 rows today have status=PASS, not FAIL.
#   (b) Every PASS row has a SKIP payload — no numeric score was emitted.
#   (c) The error "no dataset available" appears zero times today.
#   (d) Row count is non-zero (run actually happened).
#   (e) At least one SKIP reason references "polygon_fallback_active" or
#       "Option C" — confirming the authorised code path fired.
# =============================================================================

set -euo pipefail
SCRIPT_VERSION="1.0.0"

echo "################################################################"
echo "# STAGE-13 PROBABILITY ENGINE VERIFICATION"
echo "# Script: verify_stage13_probability_engine.sh  v${SCRIPT_VERSION}"
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

# ── Market-hours gate ─────────────────────────────────────────────────────────
ET_HOUR=$(TZ='America/New_York' date '+%H')
ET_DOW=$(TZ='America/New_York' date '+%u')   # 1=Mon … 7=Sun
echo ""
if [[ "$ET_DOW" -ge 6 ]]; then
  echo "[WARN] Today is a weekend — paper-trade pipeline does not run. Stage-13 rows"
  echo "       will be from a prior weekday. Adjust \$DB_TODAY manually if needed."
fi
if [[ "$ET_HOUR" -lt 9 ]]; then
  echo "[WARN] Before 09:00 ET — 09:42 cron has not fired yet. Re-run after 09:45."
fi

echo ""
echo "================================================================"
echo "PART A — Stage-13 row count for \$DB_TODAY"
echo "================================================================"
psql "$DATABASE_URL" -x << SQL
SELECT
    stage_order,
    COUNT(*)                                                      AS total_rows,
    COUNT(*) FILTER (WHERE status = 'PASS')                       AS pass_count,
    COUNT(*) FILTER (WHERE status = 'FAIL')                       AS fail_count,
    COUNT(*) FILTER (WHERE status NOT IN ('PASS','FAIL'))         AS other_count
FROM aiem_diagram2_trace_audit
WHERE stage_order = 13
  AND completed_at::date = current_date
GROUP BY stage_order;
SQL

echo ""
echo "================================================================"
echo "PART B — Each Stage-13 PASS row: candidate_id + skip payload"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    LEFT(trace_id, 36)            AS trace_id,
    ticker,
    status,
    candidate_id,
    completed_at                  AS at,
    LEFT(payload_json::text, 200) AS payload_prefix
FROM aiem_diagram2_trace_audit
WHERE stage_order    = 13
  AND completed_at::date = current_date
ORDER BY completed_at;
SQL

echo ""
echo "================================================================"
echo "PART C — FAIL rows today (must be zero)"
echo "================================================================"
FAIL_COUNT=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM aiem_diagram2_trace_audit
  WHERE stage_order = 13
    AND status = 'FAIL'
    AND completed_at::date = current_date;
" | xargs)
echo "Stage-13 FAIL rows today: $FAIL_COUNT"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  echo "  → PASS ✅  (no FAILs today)"
else
  echo "  → FAIL ❌  ($FAIL_COUNT FAILs — pull full error_message below)"
  psql "$DATABASE_URL" << SQL
SELECT ticker, status, LEFT(error_message,300) AS error_message, completed_at
FROM aiem_diagram2_trace_audit
WHERE stage_order=13 AND status='FAIL' AND completed_at::date=current_date
ORDER BY completed_at;
SQL
fi

echo ""
echo "================================================================"
echo "PART D — Old error string must not appear today"
echo "================================================================"
OLD_ERR_COUNT=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM aiem_diagram2_trace_audit
  WHERE stage_order = 13
    AND error_message ILIKE '%no dataset available%'
    AND completed_at::date = current_date;
" | xargs)
echo "'no dataset available' occurrences today: $OLD_ERR_COUNT"
[[ "$OLD_ERR_COUNT" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌ (old error still firing)"

echo ""
echo "================================================================"
echo "PART E — SKIP reason must reference authorised Option-C path"
echo "================================================================"
SKIP_REASON_COUNT=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM aiem_diagram2_trace_audit
  WHERE stage_order = 13
    AND completed_at::date = current_date
    AND (
        payload_json::text ILIKE '%polygon_fallback_active%'
     OR payload_json::text ILIKE '%Option C%'
     OR error_message      ILIKE '%polygon_fallback_active%'
    );
" | xargs)
echo "Rows with authorised Option-C skip reason: $SKIP_REASON_COUNT"
[[ "$SKIP_REASON_COUNT" -gt 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌ (no Option-C reason found)"

echo ""
echo "================================================================"
echo "PART F — Log corroboration (live process log grep)"
echo "================================================================"
LOG_FILE=$(ls -t /tmp/logs/stock-api_*.log 2>/dev/null | head -1)
if [[ -n "$LOG_FILE" ]]; then
  echo "Scanning: $LOG_FILE"
  grep -i "stage.13\|probability_engine\|polygon_fallback\|SKIP" "$LOG_FILE" 2>/dev/null | tail -20 || echo "  (no matching lines)"
else
  echo "  (no stock-api log file found under /tmp/logs/)"
fi

echo ""
echo "################################################################"
echo "FINAL VERDICT CRITERIA — check each line manually"
echo "################################################################"
echo "  [ ] PART A: total_rows > 0 AND fail_count = 0"
echo "  [ ] PART B: every row has status=PASS AND candidate_id IS NOT NULL"
echo "  [ ] PART B: payload_prefix contains 'SKIP' or 'polygon_fallback'"
echo "  [ ] PART C: FAIL rows today = 0"
echo "  [ ] PART D: 'no dataset available' occurrences = 0"
echo "  [ ] PART E: Option-C skip reason count > 0"
echo ""
echo "ALL SIX = PASS → Fix 1 (Stage 13) is live and operating correctly."
echo "ANY FAIL       → Pull PART B payload_prefix + error_message and report."
echo "################################################################"
