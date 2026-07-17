#!/usr/bin/env bash
# verified_run.sh — AIEM Failover Evidence Capture Script
# Tamper-evident: sha256 of this script is embedded in output bundle.
# Usage: bash verified_run.sh [GH_RUN_ID]
#
# Run AFTER the automated GitHub Actions trigger has fired.
# Captures: file hashes, line numbers, HTTP security tests,
#           DB state, GitHub Actions run record, live checkpoint.
# Output: verified_evidence_<date>_<time>.json

set -euo pipefail

SCRIPT_SHA=$(sha256sum "$0" | awk '{print $1}')
VERIFY_SHA=$(sha256sum verify_chain.sh 2>/dev/null | awk '{print $1}' || echo "verify_chain.sh not found")
CAPTURE_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
TODAY=$(date -u +"%Y-%m-%d")
OUT="verified_evidence_${TODAY}_$(date -u +%H%M%S).json"

echo "=== AIEM Failover Evidence Capture ==="
echo "Capture time : $CAPTURE_TIME"
echo "Script SHA   : $SCRIPT_SHA"
echo "verify_chain : $VERIFY_SHA"
echo "Output file  : $OUT"
echo ""

# ── 1. File hashes of all changed files ──────────────────────────────────────
echo "[1/7] Hashing changed files..."
MAIN_SHA=$(sha256sum artifacts/stock-scanner-api/main.py | awk '{print $1}')
RUNNER_SHA=$(sha256sum artifacts/stock-scanner-api/aiem_backup_runner.py | awk '{print $1}')
WD_SHA=$(sha256sum .github/workflows/market-hours-watchdog.yml | awk '{print $1}')
MB_SHA=$(sha256sum .github/workflows/morning-backup.yml | awk '{print $1}')

# ── 2. Line-number verification ──────────────────────────────────────────────
echo "[2/7] Verifying line numbers..."
CHECKPOINT_LINE=$(grep -n "def admin_pipeline_checkpoint" artifacts/stock-scanner-api/main.py | cut -d: -f1)
EMERGENCY_LINE=$(grep -n "def admin_emergency_run" artifacts/stock-scanner-api/main.py | cut -d: -f1)
PRECHECK_LINE=$(grep -n "WHERE trade_date = %s AND ticker = %s$" artifacts/stock-scanner-api/aiem_backup_runner.py | cut -d: -f1)
ROLLBACK_LINE=$(grep -n "conn.rollback" artifacts/stock-scanner-api/aiem_backup_runner.py | cut -d: -f1)

# ── 3. DB state snapshot ─────────────────────────────────────────────────────
echo "[3/7] Querying DB state..."
DB_JOBS=$(python3 -c "
import psycopg2, os, json
db = os.environ['DATABASE_URL']
with psycopg2.connect(db) as conn, conn.cursor() as cur:
    cur.execute(\"SELECT ticker, status, completed_at::text FROM options_pipeline_jobs WHERE scan_date=CURRENT_DATE ORDER BY ticker\")
    print(json.dumps([{'ticker':r[0],'status':r[1],'completed_at':r[2]} for r in cur.fetchall()]))
")

DB_DPR=$(python3 -c "
import psycopg2, os, json
db = os.environ['DATABASE_URL']
with psycopg2.connect(db) as conn, conn.cursor() as cur:
    cur.execute(\"SELECT run_date::text, status, trigger_source, created_at::text, completed_at::text FROM daily_pipeline_runs WHERE run_date=CURRENT_DATE LIMIT 1\")
    r = cur.fetchone()
    print(json.dumps({'run_date':r[0],'status':r[1],'trigger_source':r[2],'created_at':r[3],'completed_at':r[4]} if r else None))
")

DB_TRADES=$(python3 -c "
import psycopg2, os, json
db = os.environ['DATABASE_URL']
with psycopg2.connect(db) as conn, conn.cursor() as cur:
    cur.execute(\"SELECT id, ticker, trade_type, signal_source, entry_price::text, status, created_at::text FROM aiem_paper_trades WHERE trade_date=CURRENT_DATE ORDER BY id\")
    print(json.dumps([{'id':r[0],'ticker':r[1],'trade_type':r[2],'signal_source':r[3],'entry_price':r[4],'status':r[5],'created_at':r[6]} for r in cur.fetchall()]))
")

# ── 4. GitHub Actions run log ─────────────────────────────────────────────────
echo "[4/7] Fetching GitHub Actions run log..."
GH_RUN_ID="${1:-}"
GH_TOKEN=$(python3 -c "
import os
try:
    with open(os.path.expanduser('~/.config/gh/hosts.yml')) as f:
        for l in f:
            if 'oauth_token:' in l:
                print(l.split('oauth_token:',1)[1].strip()); break
except: print('')
")

if [ -n "$GH_TOKEN" ]; then
    if [ -z "$GH_RUN_ID" ]; then
        GH_RUNS=$(curl -sf -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/jdcarlo1/aiem-watchdog/actions/workflows/market-hours-watchdog.yml/runs?per_page=5&event=schedule" 2>/dev/null || echo '{}')
        GH_RUN_ID=$(echo "$GH_RUNS" | python3 -c "
import sys,json; d=json.load(sys.stdin)
runs=d.get('workflow_runs',[])
print(runs[0]['id'] if runs else '')
" 2>/dev/null || echo "")
        # Also check morning-backup
        if [ -z "$GH_RUN_ID" ]; then
            MB_RUNS=$(curl -sf -H "Authorization: token $GH_TOKEN" \
                "https://api.github.com/repos/jdcarlo1/aiem-watchdog/actions/workflows/morning-backup.yml/runs?per_page=5&event=schedule" 2>/dev/null || echo '{}')
            GH_RUN_ID=$(echo "$MB_RUNS" | python3 -c "
import sys,json; d=json.load(sys.stdin)
runs=d.get('workflow_runs',[])
print(runs[0]['id'] if runs else '')
" 2>/dev/null || echo "")
        fi
    fi

    if [ -n "$GH_RUN_ID" ]; then
        GH_RUN_JSON=$(curl -sf -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/jdcarlo1/aiem-watchdog/actions/runs/$GH_RUN_ID" 2>/dev/null || echo '{}')
        GH_TRIGGER=$(echo "$GH_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('event',''))" 2>/dev/null || echo "")
        GH_STATUS=$(echo "$GH_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('conclusion',''))" 2>/dev/null || echo "")
        GH_CREATED=$(echo "$GH_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('created_at',''))" 2>/dev/null || echo "")
        GH_URL=$(echo "$GH_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('html_url',''))" 2>/dev/null || echo "")
    else
        GH_TRIGGER="no_run_found"; GH_STATUS=""; GH_CREATED=""; GH_URL=""; GH_RUN_JSON="{}"
    fi
else
    GH_TRIGGER="token_unavailable"; GH_STATUS=""; GH_CREATED=""; GH_URL=""; GH_RUN_JSON="{}"
fi

# ── 5. HTTP security tests ────────────────────────────────────────────────────
echo "[5/7] Running HTTP security tests (all four cases)..."
BASE_URL="${REPLIT_APP_URL:-https://hello-world-2-joeldcarlo.replit.app}"
ADMIN_TOK="${ADMIN_TOKEN:-}"

# T1: Wrong token → must return 403
T1_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "$BASE_URL/stock-api/admin/emergency-run" \
  -H "X-Admin-Token: wrong_token_intentionally_bad" \
  -H "Content-Type: application/json" \
  -d '{"ts":1}' --max-time 10 2>/dev/null || echo "000")
T1_PASS="FAIL:got_${T1_CODE}"; [ "$T1_CODE" = "403" ] && T1_PASS="PASS"
echo "  T1 wrong-token → ${T1_CODE} : ${T1_PASS}"

# T2: Correct token, stale timestamp (ts=1) → must return 400
T2_CODE=$(curl -s -o /tmp/_t2_body.txt -w "%{http_code}" -X POST \
  "$BASE_URL/stock-api/admin/emergency-run" \
  -H "X-Admin-Token: ${ADMIN_TOK}" \
  -H "Content-Type: application/json" \
  -d '{"ts":1}' --max-time 10 2>/dev/null || echo "000")
T2_BODY=$(cat /tmp/_t2_body.txt 2>/dev/null || echo '{}')
T2_PASS="FAIL:got_${T2_CODE}"; [ "$T2_CODE" = "400" ] && T2_PASS="PASS"
echo "  T2 stale-ts   → ${T2_CODE} : ${T2_PASS}"

# T3: Correct token, fresh timestamp → must return 200 with status field
T3_TS=$(date +%s)
T3_RESP=$(curl -sf -X POST \
  "$BASE_URL/stock-api/admin/emergency-run" \
  -H "X-Admin-Token: ${ADMIN_TOK}" \
  -H "Content-Type: application/json" \
  -d "{\"ts\": ${T3_TS}}" --max-time 90 2>/dev/null || echo '{"error":"curl_failed"}')
T3_STATUS=$(echo "$T3_RESP" | python3 -c "
import sys,json
try: d=json.load(sys.stdin); print(d.get('status','MISSING'))
except: print('parse_error')
")
T3_PASS="FAIL:status_${T3_STATUS}"
[ "$T3_STATUS" = "COMPLETED" ] && T3_PASS="PASS"
[ "$T3_STATUS" = "NO_ACTION"  ] && T3_PASS="PASS"
echo "  T3 valid-auth  → status=${T3_STATUS} : ${T3_PASS}"

# T4: No auth, checkpoint endpoint → must return 200 with date field
T4_RESP=$(curl -sf \
  "$BASE_URL/stock-api/admin/pipeline-checkpoint" --max-time 10 2>/dev/null || echo '{"error":"curl_failed"}')
T4_DATE=$(echo "$T4_RESP" | python3 -c "
import sys,json
try: d=json.load(sys.stdin); print(d.get('date','MISSING'))
except: print('parse_error')
")
T4_PASS="FAIL:no_date_${T4_DATE}"
[ "$T4_DATE" != "MISSING" ] && [ "$T4_DATE" != "parse_error" ] && T4_PASS="PASS"
echo "  T4 checkpoint  → date=${T4_DATE} : ${T4_PASS}"

# ── 6. Live checkpoint endpoint (final state) ─────────────────────────────────
echo "[6/7] Calling live checkpoint endpoint (final state)..."
CHECKPOINT=$(curl -sf "$BASE_URL/stock-api/admin/pipeline-checkpoint" --max-time 10 2>/dev/null || echo '{"error":"unreachable"}')

# ── 7. Assemble evidence bundle ───────────────────────────────────────────────
echo "[7/7] Writing evidence bundle..."
python3 - << PYEOF
import json, os

bundle = {
    "evidence_version": "1.1",
    "capture_time_utc": "$CAPTURE_TIME",
    "script_sha256": "$SCRIPT_SHA",
    "verify_chain_sha256": "$VERIFY_SHA",
    "date": "$TODAY",

    "file_hashes": {
        "main.py":                   "$MAIN_SHA",
        "aiem_backup_runner.py":     "$RUNNER_SHA",
        "market-hours-watchdog.yml": "$WD_SHA",
        "morning-backup.yml":        "$MB_SHA",
    },

    "line_numbers": {
        "admin_pipeline_checkpoint_def": $CHECKPOINT_LINE,
        "admin_emergency_run_def":       $EMERGENCY_LINE,
        "backup_runner_precheck_fix":    "$PRECHECK_LINE",
        "backup_runner_rollback_fix":    "$ROLLBACK_LINE",
    },

    "db_state": {
        "options_pipeline_jobs": json.loads(r'''$DB_JOBS'''),
        "daily_pipeline_runs":   json.loads(r'''$DB_DPR'''),
        "paper_trades":          json.loads(r'''$DB_TRADES'''),
    },

    "github_actions": {
        "run_id":    "$GH_RUN_ID",
        "event":     "$GH_TRIGGER",
        "status":    "$GH_STATUS",
        "created_at":"$GH_CREATED",
        "html_url":  "$GH_URL",
    },

    "http_tests": {
        "t1_wrong_token":    {"expected_http": "403", "actual_http": "$T1_CODE", "result": "$T1_PASS"},
        "t2_stale_timestamp":{"expected_http": "400", "actual_http": "$T2_CODE", "result": "$T2_PASS",
                              "response_body": json.loads(r'''$T2_BODY''' or '{}')},
        "t3_valid_auth":     {"expected_status": "COMPLETED|NO_ACTION", "actual_status": "$T3_STATUS",
                              "result": "$T3_PASS",
                              "response": json.loads(r'''$T3_RESP''' or '{}')},
        "t4_checkpoint_noauth": {"expected_date": "$TODAY", "actual_date": "$T4_DATE", "result": "$T4_PASS"},
    },

    "live_checkpoint": json.loads(r'''$CHECKPOINT'''),
}

with open("$OUT", "w") as f:
    json.dump(bundle, f, indent=2)
print(json.dumps(bundle, indent=2))
PYEOF

echo ""
echo "Evidence bundle written to: $OUT"
echo "Run: bash verify_chain.sh $OUT"
