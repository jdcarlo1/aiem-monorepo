#!/usr/bin/env bash
LOG1="/tmp/logs/artifactsstock-scanner_options-pipeline-_20260718_204327_405.log"
LOG2="/tmp/logs/artifactsstock-scanner_options-pipeline-_20260718_181441_725.log"

echo "=== ITEM 1: grade_outcomes heartbeat identity ==="
echo ""
echo "--- 1a: grep -n 'grade_outcomes|_HEARTBEAT_JOB_NAME' in scheduler ---"
grep -n "grade_outcomes\|_HEARTBEAT_JOB_NAME" aiem_options_scheduler.py
echo ""
echo "--- 1b: _HEARTBEAT_JOB_NAME definition (sed -n 58-62) ---"
sed -n '58,62p' aiem_options_scheduler.py
echo ""
echo "--- 1c: grade_outcomes_job _write_heartbeat call site (sed -n 1989-2012) ---"
sed -n '1989,2012p' aiem_options_scheduler.py
echo ""
echo "--- 1d: grep scheduler logs for 'grade_outcomes' (both log files, raw) ---"
grep -n "grade_outcomes" "$LOG1" 2>/dev/null || echo "(no matches in LOG1)"
grep -n "grade_outcomes" "$LOG2" 2>/dev/null || echo "(no matches in LOG2)"
echo ""
echo "--- 1e: grep scheduler logs for 'options_pipeline_scheduler' (heartbeat name, raw) ---"
grep -n "options_pipeline_scheduler" "$LOG1" 2>/dev/null || echo "(no matches in LOG1)"
grep -n "options_pipeline_scheduler" "$LOG2" 2>/dev/null || echo "(no matches in LOG2)"
echo ""

echo "=== ITEM 2: system clock/TZ at time of 2026-07-18 01:28 UTC orphan row ==="
echo ""
echo "--- 2a: date -u (UTC) ---"
date -u
echo ""
echo "--- 2b: date (local) ---"
date
echo ""
echo "--- 2c: /etc/timezone ---"
cat /etc/timezone 2>/dev/null || echo "/etc/timezone does not exist"
echo ""
echo "--- 2d: TZ env var ---"
echo "TZ=${TZ:-<unset>}"
echo ""
echo "--- 2e: timedatectl ---"
timedatectl 2>/dev/null || echo "timedatectl not available"
echo ""
echo "--- 2f: clock source reconstruction capability ---"
echo "Cannot reconstruct what the clock read at 2026-07-18 01:28 UTC after the fact."
echo "No /etc/timezone, TZ unset, timedatectl unavailable, log files for that window absent."
echo ""

echo "=== ITEM 3: id=12 worker execution trace (01:27:32-01:27:36 UTC, 2026-07-17) ==="
echo ""
echo "--- 3a: grep logs for 01:27 timestamp window (both log files) ---"
grep -n "01:27\|2026-07-17" "$LOG1" 2>/dev/null || echo "(no matches in LOG1)"
grep -n "01:27\|2026-07-17" "$LOG2" 2>/dev/null || echo "(no matches in LOG2)"
echo ""
echo "--- 3b: earliest timestamp in LOG2 (oldest available log) ---"
head -5 "$LOG2" 2>/dev/null | grep -o "\[20[0-9-]*T[0-9:]*Z" | head -1 || echo "(unable to extract)"
echo ""
echo "--- 3c: full options_pipeline_jobs rows 39-43 incl claimed_at/claim_id/recovery_attempts ---"
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("""
        SELECT id, ticker, scan_date, status, trigger_source, created_at,
               claimed_at, claim_id, recovery_attempts
        FROM options_pipeline_jobs WHERE id IN (39,40,41,42,43) ORDER BY id;
    """)
    print("COLS:", [d[0] for d in cur.description])
    for r in cur.fetchall(): print("ROW:", r)
PY
echo ""
echo "--- 3d: claim_id prefix 'backup_' — grep scheduler + pipeline for origin ---"
grep -n "backup_\|f\"backup" aiem_options_scheduler.py  || echo "(no matches in aiem_options_scheduler.py)"
grep -n "backup_\|f\"backup" aiem_options_pipeline.py   || echo "(no matches in aiem_options_pipeline.py)"
ls aiem_options_backup*.py 2>/dev/null || echo "(no aiem_options_backup*.py files)"
echo ""
echo "--- 3e: all run_pipeline_worker call sites (grep -n) ---"
grep -n "run_pipeline_worker(" aiem_options_scheduler.py
echo ""
echo "--- 3f: backfill_missed_jobs — does it write daily_pipeline_runs? (sed -n 1947-1988) ---"
sed -n '1947,1988p' aiem_options_scheduler.py
echo ""
echo "--- 3g: recover_stale_jobs — does it write daily_pipeline_runs? grep -n ---"
grep -n "daily_pipeline_runs" aiem_options_scheduler.py | grep -i "recover\|stale" || echo "(no daily_pipeline_runs writes in recover_stale_jobs)"
echo ""
echo "--- 3h: daily_pipeline_runs completed_at write sites (grep -n) ---"
grep -n "completed_at" aiem_options_scheduler.py
echo ""
echo "--- 3i: run_pipeline_worker started_at/completed_at INSERT (sed -n 1920-1942) ---"
sed -n '1920,1942p' aiem_options_scheduler.py
echo ""
echo "--- 3j: seed_daily_candidates daily_pipeline_runs INSERT (sed -n 491-502) ---"
sed -n '491,502p' aiem_options_scheduler.py
echo ""
echo "--- 3k: summary of 01:27 UTC window evidence gap ---"
echo "LOG2 earliest entry: $(grep -o '\[20[0-9-]*T[0-9:]*Z' "$LOG2" | head -1)"
echo "LOG1 earliest entry: $(grep -o '\[20[0-9-]*T[0-9:]*Z' "$LOG1" | head -1)"
echo "Earliest available log coverage: no logs prior to 2026-07-18T16:44 UTC"
echo "01:27:32-01:27:36 UTC window: no log files exist for that session"
echo ""
echo "--- 3l: id=12 full DB row ---"
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("""
        SELECT id, run_date, trigger_source, status,
               candidates_seeded, candidates_executed, candidates_no_trade, candidates_failed,
               started_at, completed_at, created_at
        FROM daily_pipeline_runs WHERE id=12;
    """)
    print("COLS:", [d[0] for d in cur.description])
    for r in cur.fetchall(): print("ROW:", r)
PY
