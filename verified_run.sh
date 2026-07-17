#!/usr/bin/env bash
# verified_run.sh — AIEM Failover Evidence Capture Script
# Tamper-evident: sha256 of this script is embedded in output.
# Usage: bash verified_run.sh [GH_RUN_ID]
#
# Run AFTER the automated GitHub Actions watchdog has fired.
# Captures: DB before/after state, GitHub Actions run log, file hashes.
# Output: verified_evidence_<timestamp>.json

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
echo "[1/6] Hashing changed files..."
MAIN_SHA=$(sha256sum artifacts/stock-scanner-api/main.py | awk '{print $1}')
RUNNER_SHA=$(sha256sum artifacts/stock-scanner-api/aiem_backup_runner.py | awk '{print $1}')
WD_SHA=$(sha256sum .github/workflows/market-hours-watchdog.yml | awk '{print $1}')
MB_SHA=$(sha256sum .github/workflows/morning-backup.yml | awk '{print $1}')

# ── 2. Line-number verification ──────────────────────────────────────────────
echo "[2/6] Verifying line numbers..."
CHECKPOINT_LINE=$(grep -n "def admin_pipeline_checkpoint" artifacts/stock-scanner-api/main.py | cut -d: -f1)
EMERGENCY_LINE=$(grep -n "def admin_emergency_run" artifacts/stock-scanner-api/main.py | cut -d: -f1)
PRECHECK_LINE=$(grep -n "WHERE trade_date = %s AND ticker = %s$" artifacts/stock-scanner-api/aiem_backup_runner.py | cut -d: -f1)
ROLLBACK_LINE=$(grep -n "conn.rollback" artifacts/stock-scanner-api/aiem_backup_runner.py | cut -d: -f1)

# ── 3. DB state snapshot ─────────────────────────────────────────────────────
echo "[3/6] Querying DB state..."
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
echo "[4/6] Fetching GitHub Actions run log..."
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
        # Auto-detect latest watchdog run
        GH_RUNS=$(curl -sf -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/jdcarlo1/aiem-watchdog/actions/workflows/market-hours-watchdog.yml/runs?per_page=5&event=schedule" 2>/dev/null || echo '{}')
        GH_RUN_ID=$(echo "$GH_RUNS" | python3 -c "import sys,json; d=json.load(sys.stdin); runs=d.get('workflow_runs',[]); print(runs[0]['id'] if runs else '')" 2>/dev/null || echo "")
    fi

    if [ -n "$GH_RUN_ID" ]; then
        GH_RUN_JSON=$(curl -sf -H "Authorization: token $GH_TOKEN" \
            "https://api.github.com/repos/jdcarlo1/aiem-watchdog/actions/runs/$GH_RUN_ID" 2>/dev/null || echo '{}')
        GH_TRIGGER=$(echo "$GH_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('event',''))" 2>/dev/null || echo "")
        GH_STATUS=$(echo "$GH_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('conclusion',''))" 2>/dev/null || echo "")
        GH_CREATED=$(echo "$GH_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('created_at',''))" 2>/dev/null || echo "")
    else
        GH_TRIGGER="no_run_found"; GH_STATUS=""; GH_CREATED=""; GH_RUN_JSON="{}"
    fi
else
    GH_TRIGGER="token_unavailable"; GH_STATUS=""; GH_CREATED=""; GH_RUN_JSON="{}"
fi

# ── 5. Checkpoint endpoint live check ────────────────────────────────────────
echo "[5/6] Calling live checkpoint endpoint..."
DEV_URL="${REPLIT_APP_URL:-https://hello-world-2-joeldcarlo.replit.app}"
CHECKPOINT=$(curl -sf "$DEV_URL/stock-api/admin/pipeline-checkpoint" --max-time 10 2>/dev/null || echo '{"error":"unreachable"}')

# ── 6. Assemble evidence bundle ───────────────────────────────────────────────
echo "[6/6] Writing evidence bundle..."
python3 - << PYEOF
import json, os

bundle = {
    "evidence_version": "1.0",
    "capture_time_utc": "$CAPTURE_TIME",
    "script_sha256": "$SCRIPT_SHA",
    "verify_chain_sha256": "$VERIFY_SHA",
    "date": "$TODAY",

    "file_hashes": {
        "main.py":                  "$MAIN_SHA",
        "aiem_backup_runner.py":    "$RUNNER_SHA",
        "market-hours-watchdog.yml":"$WD_SHA",
        "morning-backup.yml":       "$MB_SHA",
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
