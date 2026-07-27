#!/usr/bin/env bash
# Crash Forensics — wrapper for stock-api / main.py (Gap 1).
#
# See aiem_process_wrapper.sh for the full rationale.
# Same pattern, different process_name token.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIFECYCLE="${SCRIPT_DIR}/crash_forensics_lifecycle.py"
PYTHON="$(command -v python3 2>/dev/null || command -v python)"

# ── Record start ──────────────────────────────────────────────────────────────
"${PYTHON}" "${LIFECYCLE}" start stock-api || true

# ── Launch child in background so we can trap signals ─────────────────────────
"${PYTHON}" "${SCRIPT_DIR}/main.py" &
CHILD_PID=$!

# ── SIGTERM / SIGINT handler: forward → wait → record exit ────────────────────
_on_signal() {
    kill -TERM "${CHILD_PID}" 2>/dev/null || true
    wait "${CHILD_PID}"
    _SIG_EXIT=$?
    "${PYTHON}" "${LIFECYCLE}" exit stock-api "${_SIG_EXIT}" || true
    exit "${_SIG_EXIT}"
}
trap '_on_signal' TERM INT

# ── Wait for normal exit ──────────────────────────────────────────────────────
wait "${CHILD_PID}"
EXIT_CODE=$?

# ── Record exit (normal path — not via signal) ────────────────────────────────
"${PYTHON}" "${LIFECYCLE}" exit stock-api "${EXIT_CODE}" || true

exit "${EXIT_CODE}"
