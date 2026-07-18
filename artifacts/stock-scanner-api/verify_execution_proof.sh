#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# ── SHA256 BEFORE / AFTER ─────────────────────────────────────────────────────
echo "=== SHA256 BEFORE (from prior session) ==="
echo "aiem_options_scheduler.py  ec07184172335cc434475bc9d12aaa7ec0203e7ca950ac68b455a9665a549d64"
echo "aiem_options_pipeline.py   c9514a3382251be981f20766cf75eb71d9c81bfc463a4ab30754abf7baaec4ce"

echo ""
echo "=== SHA256 AFTER (current) ==="
sha256sum aiem_options_scheduler.py aiem_options_pipeline.py

# ── ITEM 1a: LOGGING LEVEL PROOF ─────────────────────────────────────────────
echo ""
echo "=== ITEM 1a: logging.basicConfig level in scheduler (raw grep -n) ==="
grep -n "basicConfig\|setLevel\|level=logging" aiem_options_scheduler.py | head -10

echo ""
echo "=== ITEM 1a: all [phase3] exception handlers now use log.warning (raw grep -n) ==="
grep -n "log.warning.*phase3\|log.debug.*phase3" aiem_options_scheduler.py
grep -n "phase3.*warning\|phase3.*failed\|phase3.*root_cause\|phase3.*kb_entry\|phase3.*init" aiem_options_pipeline.py

# ── ITEM 1b: SCHEDULER LOG GREP (raw) ────────────────────────────────────────
echo ""
echo "=== ITEM 1b: grep scheduler log for [phase3] (raw) ==="
SCHED_LOG=$(ls -t /tmp/logs/artifactsstock-scanner_options-pipeline-*.log 2>/dev/null | head -1)
echo "log_file: ${SCHED_LOG:-NONE}"
if [ -n "${SCHED_LOG}" ]; then
    grep -i "phase3" "${SCHED_LOG}" || echo "(no phase3 matches — log.warning not yet triggered; no _execute_job run since deploy)"
fi

echo ""
echo "=== ITEM 1b: bootstrap_phase3 direct import + execution test ==="
python3 - << 'PYEOF'
import os, sys
sys.path.insert(0, ".")
import aiem_options_phase3 as p3
db = os.environ["DATABASE_URL"]
try:
    p3.bootstrap_phase3(db)
    print("bootstrap_phase3: SUCCESS — import + schema bootstrap completed without exception")
except Exception as e:
    print(f"bootstrap_phase3: FAILED — {e}")
PYEOF

# ── ITEM 2: KB TIMESTAMPS ─────────────────────────────────────────────────────
echo ""
echo "=== ITEM 2: oe_knowledge_base rows with timestamps ==="
python3 - << 'PYEOF'
import os, psycopg2
db = os.environ["DATABASE_URL"]
with psycopg2.connect(db, connect_timeout=4) as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT kb_id, kb_type, ticker, scan_date, decision_quality,
               confidence_score, validated_out_of_sample, statistical_gate_passed,
               created_at
        FROM oe_knowledge_base
        ORDER BY created_at;
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"QUERY: SELECT kb_id,kb_type,ticker,scan_date,decision_quality,confidence_score,validated_out_of_sample,statistical_gate_passed,created_at FROM oe_knowledge_base ORDER BY created_at;")
        print(f"ROW: {r}")
        break
    for r in rows[1:]:
        print(f"ROW: {r}")
PYEOF

echo ""
echo "=== ITEM 2: oe_kb_confidence_log rows with timestamps ==="
python3 - << 'PYEOF'
import os, psycopg2
db = os.environ["DATABASE_URL"]
with psycopg2.connect(db, connect_timeout=4) as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT log_id, kb_id, old_confidence, new_confidence,
               change_direction, gate_passed, sample_size, created_at
        FROM oe_kb_confidence_log
        ORDER BY created_at;
    """)
    rows = cur.fetchall()
    print(f"QUERY: SELECT log_id,kb_id,old_confidence,new_confidence,change_direction,gate_passed,sample_size,created_at FROM oe_kb_confidence_log ORDER BY created_at;")
    for r in rows:
        print(f"ROW: {r}")
PYEOF

# ── ITEM 3: grade_outcomes_job RUN HISTORY ───────────────────────────────────
echo ""
echo "=== ITEM 3: grade_outcomes_job schedule (raw grep -n) ==="
grep -n "grade_outcomes_job\|grade_outcomes" aiem_options_scheduler.py | grep -v "def grade\|log\.\|import\|#"

echo ""
echo "=== ITEM 3: daily_pipeline_runs last 5 rows (raw SQL + result) ==="
python3 - << 'PYEOF'
import os, psycopg2
db = os.environ["DATABASE_URL"]
with psycopg2.connect(db, connect_timeout=4) as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='daily_pipeline_runs'
        ORDER BY ordinal_position;
    """)
    cols = [r[0] for r in cur.fetchall()]
    print(f"QUERY: SELECT column_name FROM information_schema.columns WHERE table_name='daily_pipeline_runs';")
    print(f"COLUMNS: {cols}")
    cur.execute("SELECT * FROM daily_pipeline_runs ORDER BY run_date DESC LIMIT 5;")
    rows = cur.fetchall()
    print(f"QUERY: SELECT * FROM daily_pipeline_runs ORDER BY run_date DESC LIMIT 5;")
    for r in rows:
        print(f"ROW: {r}")
    if not rows:
        print("RESULT: 0 rows")
PYEOF

echo ""
echo "=== ITEM 3: job_heartbeats for grade_outcomes ==="
python3 - << 'PYEOF'
import os, psycopg2
db = os.environ["DATABASE_URL"]
with psycopg2.connect(db, connect_timeout=4) as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT job_name, last_success, last_attempt, consecutive_failures
        FROM job_heartbeats
        WHERE job_name LIKE '%grade%' OR job_name LIKE '%Grade%'
        ORDER BY last_attempt DESC NULLS LAST;
    """)
    rows = cur.fetchall()
    print("QUERY: SELECT job_name,last_success,last_attempt,consecutive_failures FROM job_heartbeats WHERE job_name LIKE '%grade%' ORDER BY last_attempt DESC;")
    for r in rows:
        print(f"ROW: {r}")
    if not rows:
        print("RESULT: 0 rows — grade_outcomes has not run since scheduler bootstrap")
PYEOF

# ── ITEM 4: verify_result in dir() FIX PROOF ─────────────────────────────────
echo ""
echo "=== ITEM 4: grep -n for dir() guard pattern (old — should be 0 matches) ==="
grep -n '"verify_result" in dir()\|"stock_data" in dir()' aiem_options_scheduler.py || echo "(no matches — dir() pattern removed)"

echo ""
echo "=== ITEM 4: grep -n for locals().get replacement (new — should show lines) ==="
grep -n 'locals().get("verify_result"\|locals().get("stock_data"' aiem_options_scheduler.py

echo ""
echo "=== ITEM 4: sed -n context around locals().get lines ==="
python3 - << 'PYEOF'
lines = open("aiem_options_scheduler.py").readlines()
for i, l in enumerate(lines):
    if 'locals().get("verify_result"' in l or 'locals().get("stock_data"' in l:
        start = max(0, i - 5)
        end   = min(len(lines), i + 4)
        for j in range(start, end):
            print(f"{j+1}:{lines[j]}", end="")
        break
PYEOF

# ── SYNTAX CHECK POST-EDIT ───────────────────────────────────────────────────
echo ""
echo "=== SYNTAX CHECK post-edit ==="
python3 -c "import ast; ast.parse(open('aiem_options_scheduler.py').read()); print('SYNTAX OK  aiem_options_scheduler.py')"
python3 -c "import ast; ast.parse(open('aiem_options_pipeline.py').read()); print('SYNTAX OK  aiem_options_pipeline.py')"

echo ""
echo "=== FINAL SHA256 (post-edit) ==="
sha256sum aiem_options_scheduler.py aiem_options_pipeline.py
