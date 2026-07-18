#!/usr/bin/env bash
set -euo pipefail

LOG1="/tmp/logs/artifactsstock-scanner_options-pipeline-_20260718_181441_725.log"
LOG2="/tmp/logs/artifactsstock-scanner_options-pipeline-_20260718_204327_405.log"

echo "=== sha256sum tools/verified_run.sh tools/verify_chain.sh ==="
sha256sum tools/verified_run.sh verify_chain.sh
echo ""

echo "=== verify_chain.sh raw output ==="
bash verify_chain.sh || true
echo ""

echo "=== head -20 LOG1 (181441) ==="
head -20 "$LOG1"
echo ""

echo "=== head -20 LOG2 (204327) ==="
head -20 "$LOG2"
