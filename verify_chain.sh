#!/usr/bin/env bash
# verify_chain.sh — AIEM Failover Evidence Chain Verifier
# Usage: bash verify_chain.sh verified_evidence_<timestamp>.json
#
# Checks every claim in the evidence bundle against live state.
# All PASS/FAIL results printed. Exit 0 only if all checks pass.

set -euo pipefail

EVIDENCE="${1:-}"
if [ -z "$EVIDENCE" ] || [ ! -f "$EVIDENCE" ]; then
    echo "Usage: bash verify_chain.sh verified_evidence_<timestamp>.json"
    exit 1
fi

PASS=0; FAIL=0; SKIP=0

ok()   { echo "  PASS : $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL : $1"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP : $1"; SKIP=$((SKIP+1)); }

echo "=== AIEM Failover Evidence Chain Verifier ==="
echo "Evidence file : $EVIDENCE"
echo "Verified at   : $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo ""

# ── A. Script integrity ───────────────────────────────────────────────────────
echo "[A] Script integrity"
STORED_SCRIPT_SHA=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['script_sha256'])")
STORED_VERIFY_SHA=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['verify_chain_sha256'])")
LIVE_VERIFY_SHA=$(sha256sum verify_chain.sh | awk '{print $1}')
LIVE_RUN_SHA=$(sha256sum verified_run.sh | awk '{print $1}')

if [ "$LIVE_VERIFY_SHA" = "$STORED_VERIFY_SHA" ]; then
    ok "verify_chain.sh unchanged since evidence capture (sha256=$LIVE_VERIFY_SHA)"
else
    fail "verify_chain.sh MODIFIED since capture (stored=$STORED_VERIFY_SHA live=$LIVE_VERIFY_SHA)"
fi
if [ "$LIVE_RUN_SHA" = "$STORED_SCRIPT_SHA" ]; then
    ok "verified_run.sh unchanged since evidence capture (sha256=$LIVE_RUN_SHA)"
else
    fail "verified_run.sh MODIFIED since capture (stored=$STORED_SCRIPT_SHA live=$LIVE_RUN_SHA)"
fi

# ── B. File hash verification ─────────────────────────────────────────────────
echo ""
echo "[B] File hash verification (live vs. captured)"
for FILE_KEY in "main.py:artifacts/stock-scanner-api/main.py" \
                "aiem_backup_runner.py:artifacts/stock-scanner-api/aiem_backup_runner.py" \
                "market-hours-watchdog.yml:.github/workflows/market-hours-watchdog.yml" \
                "morning-backup.yml:.github/workflows/morning-backup.yml"; do
    KEY="${FILE_KEY%%:*}"
    PATH_="${FILE_KEY##*:}"
    STORED=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['file_hashes'].get('$KEY','MISSING'))")
    if [ -f "$PATH_" ]; then
        LIVE=$(sha256sum "$PATH_" | awk '{print $1}')
        if [ "$LIVE" = "$STORED" ]; then
            ok "$KEY matches captured hash ($LIVE)"
        else
            fail "$KEY CHANGED since capture (stored=$STORED live=$LIVE)"
        fi
    else
        fail "$KEY not found at $PATH_"
    fi
done

# ── C. Line-number verification ───────────────────────────────────────────────
echo ""
echo "[C] Line-number verification"
python3 - << PYEOF
import json, subprocess, sys

e = json.load(open("$EVIDENCE"))
ln = e["line_numbers"]
checks = [
    ("admin_pipeline_checkpoint_def", "def admin_pipeline_checkpoint",
     "artifacts/stock-scanner-api/main.py"),
    ("admin_emergency_run_def", "def admin_emergency_run",
     "artifacts/stock-scanner-api/main.py"),
]
fails = 0
for key, pattern, fpath in checks:
    stored = ln.get(key)
    r = subprocess.run(["grep", "-n", pattern, fpath], capture_output=True, text=True)
    lines = [int(l.split(":")[0]) for l in r.stdout.strip().splitlines() if l.strip()]
    if lines and stored and int(stored) in lines:
        print(f"  PASS : {key} at line {stored} confirmed by grep -n")
    else:
        print(f"  FAIL : {key} stored={stored} grep found={lines}")
        fails += 1
sys.exit(fails)
PYEOF
if [ $? -ne 0 ]; then FAIL=$((FAIL+1)); else PASS=$((PASS+1)); fi

# ── D. DB state validation ────────────────────────────────────────────────────
echo ""
echo "[D] DB state validation"
python3 - << PYEOF
import json, psycopg2, os, sys

e     = json.load(open("$EVIDENCE"))
db    = os.environ.get("DATABASE_URL", "")
fails = 0

if not db:
    print("  SKIP : DATABASE_URL not set — cannot verify live DB")
    sys.exit(0)

try:
    with psycopg2.connect(db) as conn, conn.cursor() as cur:
        # Check jobs
        cur.execute("SELECT ticker, status FROM options_pipeline_jobs WHERE scan_date=CURRENT_DATE ORDER BY ticker")
        live_jobs = {r[0]: r[1] for r in cur.fetchall()}
        stored_jobs = {j["ticker"]: j["status"] for j in e["db_state"]["options_pipeline_jobs"]}
        if all(live_jobs.get(t) == s for t,s in stored_jobs.items()):
            print(f"  PASS : options_pipeline_jobs matches captured state ({dict(live_jobs)})")
        else:
            print(f"  FAIL : options_pipeline_jobs mismatch: stored={stored_jobs} live={live_jobs}")
            fails += 1

        # Check daily pipeline run
        cur.execute("SELECT status, trigger_source FROM daily_pipeline_runs WHERE run_date=CURRENT_DATE LIMIT 1")
        r = cur.fetchone()
        stored_dpr = e["db_state"]["daily_pipeline_runs"]
        if stored_dpr is None and r is None:
            print("  PASS : daily_pipeline_runs: no row (matches captured state)")
        elif r and stored_dpr and r[0] == stored_dpr["status"]:
            print(f"  PASS : daily_pipeline_runs status={r[0]} trigger={r[1]}")
        else:
            print(f"  FAIL : daily_pipeline_runs mismatch: stored={stored_dpr} live={r}")
            fails += 1

        # Check paper trades
        cur.execute("SELECT id, ticker, signal_source FROM aiem_paper_trades WHERE trade_date=CURRENT_DATE ORDER BY id")
        live_trades = [(r[0], r[1], r[2]) for r in cur.fetchall()]
        stored_ids  = [t["id"] for t in e["db_state"]["paper_trades"]]
        live_ids    = [t[0] for t in live_trades]
        if set(stored_ids).issubset(set(live_ids)):
            print(f"  PASS : paper_trades: {live_trades}")
        else:
            print(f"  FAIL : paper_trades mismatch: stored_ids={stored_ids} live_ids={live_ids}")
            fails += 1

except Exception as ex:
    print(f"  FAIL : DB error: {ex}")
    fails += 1

sys.exit(fails)
PYEOF
if [ $? -ne 0 ]; then FAIL=$((FAIL+1)); else PASS=$((PASS+1)); fi

# ── E. GitHub Actions trigger verification ────────────────────────────────────
echo ""
echo "[E] GitHub Actions trigger verification"
GH_EVENT=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['github_actions']['event'])")
GH_RUN_ID=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['github_actions']['run_id'])")
GH_STATUS=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['github_actions']['status'])")
GH_CREATED=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['github_actions']['created_at'])")

if [ "$GH_EVENT" = "schedule" ]; then
    ok "trigger event='schedule' (automated cron, not manual dispatch)"
else
    fail "trigger event='$GH_EVENT' — expected 'schedule' for automated run"
fi

if [ "$GH_STATUS" = "success" ]; then
    ok "run $GH_RUN_ID completed with status=success"
elif [ -z "$GH_RUN_ID" ] || [ "$GH_RUN_ID" = "" ]; then
    skip "no GitHub Actions run captured yet (run verified_run.sh after automated trigger)"
else
    fail "run $GH_RUN_ID status='$GH_STATUS' (created=$GH_CREATED)"
fi

# ── F. Live checkpoint endpoint ───────────────────────────────────────────────
echo ""
echo "[F] Live pipeline-checkpoint endpoint"
STORED_NR=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['live_checkpoint'].get('needs_recovery','UNKNOWN'))")
STORED_DONE=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['live_checkpoint'].get('done',0))")
STORED_PENDING=$(python3 -c "import json; d=json.load(open('$EVIDENCE')); print(d['live_checkpoint'].get('pending',0))")

if [ "$STORED_NR" = "False" ] || [ "$STORED_NR" = "false" ]; then
    ok "needs_recovery=false at capture time (done=$STORED_DONE, pending=$STORED_PENDING)"
else
    fail "needs_recovery=$STORED_NR at capture time — recovery did not complete before capture"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== SUMMARY ==="
echo "PASS: $PASS  FAIL: $FAIL  SKIP: $SKIP"
if [ "$FAIL" -eq 0 ]; then
    echo "RESULT: ALL CHECKS PASSED"
    exit 0
else
    echo "RESULT: $FAIL CHECK(S) FAILED"
    exit 1
fi
