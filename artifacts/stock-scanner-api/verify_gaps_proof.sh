#!/usr/bin/env bash
set -euo pipefail

echo "=== CANONICAL SHA CHECK ==="
sha256sum tools/verified_run.sh verify_chain.sh
echo ""

echo "=== verify_chain.sh ==="
bash verify_chain.sh || true
echo ""

echo "=== GAP 1: SHA 656bc1ff origin ==="
echo "--- command that produced it ---"
echo "sha256sum /home/runner/workspace/phase3_followup2_proof.txt"
echo "--- raw output ---"
sha256sum /home/runner/workspace/phase3_followup2_proof.txt
echo ""
echo "--- /home/runner/workspace/phase3_followup2_proof.txt is the file written by:"
echo "    bash tools/verified_run.sh 'bash verify_followup2_proof.sh' 2>&1 | tee /home/runner/workspace/phase3_followup2_proof.txt"
echo "--- the sha256(log)=7d28915a... in SEQ=18 is verified_run.sh internal evidence_chain.log, a different file ---"
echo "--- these are two distinct files; 656bc1ff is the SHA of phase3_followup2_proof.txt (the tee output file) ---"
echo ""

echo "=== GAP 2a: full _atomic_claim WHERE clause (sed -n 600-640) ==="
sed -n '600,640p' aiem_options_scheduler.py
echo ""

echo "=== GAP 2b: missed-seed gate incl _before_eod def/assign (sed -n 2108,2143) ==="
sed -n '2108,2143p' aiem_options_scheduler.py
echo ""

echo "=== GAP 2c: 09:45 ET cron registration incl day_of_week (sed -n 2148,2160) ==="
sed -n '2148,2160p' aiem_options_scheduler.py
echo ""

echo "=== GAP 2d: test-cycle scan_date assignment + call site (sed -n 2197,2237) ==="
sed -n '2197,2237p' aiem_options_scheduler.py
echo ""

echo "=== GAP 3: job_heartbeats schema + SELECT * WHERE job_name='aiem_prediction_grader' ==="
python3 - << 'PY'
import os, psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as c, c.cursor() as cur:
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name='job_heartbeats'
        ORDER BY ordinal_position;
    """)
    print("SCHEMA:")
    for r in cur.fetchall(): print(" ", r)
    cur.execute("SELECT * FROM job_heartbeats WHERE job_name='aiem_prediction_grader';")
    print("COLS:", [d[0] for d in cur.description])
    rows = cur.fetchall()
    if rows:
        for r in rows: print("ROW:", r)
    else:
        print("(no rows)")
PY
