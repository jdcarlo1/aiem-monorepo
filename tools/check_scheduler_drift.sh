#!/usr/bin/env bash
# tools/check_scheduler_drift.sh
# ─────────────────────────────────────────────────────────────────────────────
# Compare the commit the running options-pipeline-scheduler loaded at boot
# against the current git HEAD on disk.
#
# Usage:  bash tools/check_scheduler_drift.sh [PORT]
#   PORT defaults to 5053 (OPTIONS_SCHEDULER_PORT)
#
# Exit codes:
#   0 = MATCH  (process is running current code)
#   1 = STALE  (a deploy happened after this process started — restart required)
#   2 = ERROR  (scheduler not reachable or health endpoint missing boot_commit)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PORT="${1:-5053}"
DISK=$(git rev-parse HEAD 2>/dev/null || echo "UNKNOWN")

HEALTH=$(curl -sf "http://localhost:${PORT}/health" 2>/dev/null) || {
    echo "ERROR: scheduler not responding on port ${PORT}"
    exit 2
}

BOOT=$(echo "$HEALTH"  | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('boot_commit','UNKNOWN'))")
DRIFT=$(echo "$HEALTH" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('drift_minutes', ''))")

echo "RUNNING : ${BOOT}"
echo "ON-DISK : ${DISK}"

if [ "$BOOT" = "UNKNOWN" ]; then
    echo "STATUS  : ERROR — scheduler did not expose boot_commit (old process?)"
    exit 2
fi

if [ "$BOOT" = "$DISK" ]; then
    echo "STATUS  : MATCH — process is running current code"
    exit 0
else
    echo "STATUS  : STALE — PROCESS IS RUNNING OLD CODE, RESTART REQUIRED"
    [ -n "$DRIFT" ] && echo "DRIFT   : ${DRIFT} minutes since process last started"
    exit 1
fi
