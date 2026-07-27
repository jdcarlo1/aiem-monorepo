#!/usr/bin/env bash
# Crash Forensics — wrapper for aiem-process (Gap 1).
#
# Records process start and exit to process_lifecycle_log via a fresh
# psycopg2.connect() in crash_forensics_lifecycle.py — completely
# independent of the app's connection pool.
#
# Why a shell wrapper (not in-process atexit):
#   SIGKILL (exit code 137, issued by the kernel OOM killer) cannot be
#   caught by any in-process Python handler.  Only the parent shell
#   observes $? after the child is killed.  That single exit code is
#   the only reliable post-hoc OOM signal when dmesg is inaccessible.
#
# Lifecycle helper failures are non-fatal (|| true).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIFECYCLE="${SCRIPT_DIR}/crash_forensics_lifecycle.py"
PYTHON="$(command -v python3 2>/dev/null || command -v python)"

# ── Record start ──────────────────────────────────────────────────────────────
"${PYTHON}" "${LIFECYCLE}" start aiem-process || true

# ── Run the process ───────────────────────────────────────────────────────────
"${PYTHON}" "${SCRIPT_DIR}/aiem_process.py"
EXIT_CODE=$?

# ── Record exit ───────────────────────────────────────────────────────────────
"${PYTHON}" "${LIFECYCLE}" exit aiem-process "${EXIT_CODE}" || true

exit "${EXIT_CODE}"
