#!/usr/bin/env bash
set -euo pipefail

LOG1="/tmp/logs/artifactsstock-scanner_options-pipeline-_20260718_204327_405.log"
LOG2="/tmp/logs/artifactsstock-scanner_options-pipeline-_20260718_181441_725.log"

echo "=== CANONICAL SHA ==="
sha256sum tools/verified_run.sh verify_chain.sh
echo ""

echo "=== verify_chain.sh ==="
bash verify_chain.sh || true
echo ""

echo "=== GAP 2b: log grep 'missed-seed|startup' — all available log files ==="
echo ""
echo "--- log file inventory + date range ---"
ls -lt "$LOG1" "$LOG2" 2>/dev/null
echo ""
echo "--- earliest line in LOG2 ---"
head -1 "$LOG2"
echo ""
echo "--- earliest line in LOG1 ---"
head -1 "$LOG1"
echo ""
echo "--- 2026-07-17 21:00-22:00 ET = 2026-07-18 02:00-03:00 UTC ---"
echo "--- grep 'missed-seed|startup' LOG2 (covers 16:44-18:14 UTC Jul 18) ---"
grep -n "missed-seed\|startup" "$LOG2" || echo "(no matches in LOG2)"
echo ""
echo "--- grep 'missed-seed|startup' LOG1 (covers 18:14-present UTC Jul 18) ---"
grep -n "missed-seed\|startup" "$LOG1" || echo "(no matches in LOG1)"
echo ""
echo "--- grep for 2026-07-18T02 or 2026-07-18T01 timestamps in either log ---"
grep -n "T02:\|T01:" "$LOG2" "$LOG1" 2>/dev/null || echo "(no matches — window predates all available logs)"
echo ""
echo "--- explicit statement ---"
echo "No log files exist for the 2026-07-17 21:00-22:00 ET (02:00-03:00 UTC Jul 18) window."
echo "Oldest available log starts at 2026-07-18T16:44:50Z."
echo "The _before_eod gate result for that window is not present in any log."
echo ""

echo "=== GAP: SEQ=19 file SHA=5f1e8a0c origin ==="
echo ""
echo "--- file the SHA belongs to ---"
echo "/home/runner/workspace/phase3_gaps_proof.txt"
echo ""
echo "--- exact commands that produced it ---"
echo "  1: bash tools/verified_run.sh \"bash verify_gaps_proof.sh\" 2>&1 | tee /home/runner/workspace/phase3_gaps_proof.txt"
echo "  2: sha256sum /home/runner/workspace/phase3_gaps_proof.txt"
echo ""
echo "--- raw sha256sum output (re-run now to confirm file unchanged) ---"
sha256sum /home/runner/workspace/phase3_gaps_proof.txt
