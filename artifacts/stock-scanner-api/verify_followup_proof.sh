#!/usr/bin/env bash
LOG_F="/tmp/logs/artifactsstock-scanner_options-pipeline-_20260718_204327_405.log"

echo "=== ITEM 1: grade_outcomes — did it fire today? ==="
echo ""
echo "--- 1a: current system date (UTC) ---"
date -u
echo ""
echo "--- 1b: CronTrigger schedule (sed -n 2189-2193) ---"
sed -n '2189,2193p' aiem_options_scheduler.py
echo ""
echo "--- 1c: grep scheduler log for 'grade_outcomes' (raw) ---"
grep -n "grade_outcomes\|grade outcomes" "$LOG_F" 2>/dev/null || echo "(no matches)"
echo ""
echo "--- 1d: job_heartbeats re-query (raw SQL) ---"
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("SELECT job_name,last_success,last_attempt,last_error,consecutive_failures FROM job_heartbeats ORDER BY last_attempt DESC NULLS LAST;")
    print("COLS:", [d[0] for d in cur.description])
    for r in cur.fetchall(): print("ROW:", r)
PY
echo ""
echo "--- 1e: _write_heartbeat success path — does it clear last_error? (sed -n 295-315) ---"
sed -n '295,315p' aiem_options_scheduler.py
echo ""

echo "=== ITEM 2: run_date=2026-07-19, created_at=2026-07-18 01:28 UTC ==="
echo ""
echo "--- 2a: _today_et startup assignment (sed -n 2082-2094) ---"
sed -n '2082,2094p' aiem_options_scheduler.py
echo ""
echo "--- 2b: timezone + date.today() usage (grep -n) ---"
grep -n "^import pytz\|^_ET\b\|date\.today()" aiem_options_scheduler.py | head -10
echo ""
echo "--- 2c: TEST_CYCLE env vars current state ---"
python3 -c "import os; print('TEST_CYCLE_OFFSET_SECS:', repr(os.environ.get('TEST_CYCLE_OFFSET_SECS','<unset>'))); print('TEST_SCAN_DATE:', repr(os.environ.get('TEST_SCAN_DATE','<unset>')))"
echo ""
echo "--- 2d: TEST_CYCLE scan_date assignment (sed -n 2205-2225) ---"
sed -n '2205,2225p' aiem_options_scheduler.py
echo ""
echo "--- 2e: all seed_daily_candidates call sites (grep -n) ---"
grep -n "seed_daily_candidates(" aiem_options_scheduler.py || echo "(no matches)"
echo ""
echo "--- 2f: options_pipeline_jobs July-19 (raw SQL) ---"
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("SELECT id,ticker,scan_date,status,trigger_source,created_at FROM options_pipeline_jobs WHERE scan_date='2026-07-19' ORDER BY created_at;")
    rows = cur.fetchall()
    print("July-19 rows:", len(rows))
    for r in rows: print("ROW:", r)
PY
echo ""
echo "--- 2g: daily_pipeline_runs full current state (raw SQL) ---"
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("SELECT id,run_date,trigger_source,status,candidates_seeded,candidates_executed,candidates_no_trade,candidates_failed,started_at,completed_at,created_at FROM daily_pipeline_runs ORDER BY id DESC LIMIT 7;")
    print("COLS:", [d[0] for d in cur.description])
    for r in cur.fetchall(): print("ROW:", r)
PY
echo ""

echo "=== ITEM 3: row id=12 (2026-07-17) — all zeros, trace each step ==="
echo ""
echo "--- 3a: options_pipeline_jobs July-17 (raw SQL) ---"
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("SELECT id,ticker,scan_date,status,trigger_source,created_at FROM options_pipeline_jobs WHERE scan_date='2026-07-17' ORDER BY created_at;")
    print("COLS:", [d[0] for d in cur.description])
    for r in cur.fetchall(): print("ROW:", r)
PY
echo ""
echo "--- 3b: OSS/PMD row counts July-17 (raw SQL) ---"
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM options_structure_scan WHERE scan_date='2026-07-17';")
    print("OSS July-17 total:", cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM options_structure_scan WHERE scan_date='2026-07-17' AND pc_skew_pp IS NOT NULL AND front_iv > 0 AND spot > 10;")
    print("OSS July-17 seed-filtered:", cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM polygon_market_daily WHERE scan_date='2026-07-17';")
    print("PMD July-17:", cur.fetchone()[0])
    cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily;")
    print("PMD MAX scan_date:", cur.fetchone()[0])
PY
echo ""
echo "--- 3c: seed ON CONFLICT DO NOTHING (sed -n 460-476) ---"
sed -n '460,476p' aiem_options_scheduler.py
echo ""
echo "--- 3d: oss_rows column — grep scheduler + pipeline (0 matches = never written) ---"
grep -n "oss_rows" aiem_options_scheduler.py || echo "(no matches in aiem_options_scheduler.py)"
grep -n "oss_rows" aiem_options_pipeline.py  || echo "(no matches in aiem_options_pipeline.py)"
echo ""
echo "--- 3e: _atomic_claim PENDING gate (sed -n 608-630) ---"
sed -n '608,630p' aiem_options_scheduler.py
echo ""
echo "--- 3f: run_pipeline_worker final_status logic (sed -n 1908-1920) ---"
sed -n '1908,1920p' aiem_options_scheduler.py
echo ""
echo "--- 3g: seed ON CONFLICT forces status=RUNNING (sed -n 495-505) ---"
sed -n '495,505p' aiem_options_scheduler.py
echo ""
echo "--- 3h: worker ON CONFLICT sets status to final_status (sed -n 1924-1935) ---"
sed -n '1924,1935p' aiem_options_scheduler.py
echo ""
echo "--- 3i: 'missing Polygon/OSS data' raise site (sed -n 755-770) ---"
sed -n '755,770p' aiem_options_scheduler.py
echo ""
echo "--- 3j: _write_heartbeat success path does NOT update last_error (sed -n 295-315) ---"
sed -n '295,315p' aiem_options_scheduler.py
