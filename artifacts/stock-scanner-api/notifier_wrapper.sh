#!/usr/bin/env bash
# Crash Forensics — wrapper for aiem-telegram (notifier process).
#
# See aiem_process_wrapper.sh for the full rationale.
# Same pattern, different process_name token.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIFECYCLE="${SCRIPT_DIR}/crash_forensics_lifecycle.py"
PYTHON="$(command -v python3 2>/dev/null || command -v python)"
NOTIFIER="/home/runner/workspace/aiem_telegram_notifier.py"

# ── Record start ──────────────────────────────────────────────────────────────
"${PYTHON}" "${LIFECYCLE}" start notifier || true

# ── Launch child in background so we can trap signals ─────────────────────────
"${PYTHON}" "${NOTIFIER}" &
CHILD_PID=$!

# ── SIGTERM / SIGINT handler: forward → wait → record exit ────────────────────
_on_signal() {
    kill -TERM "${CHILD_PID}" 2>/dev/null || true
    wait "${CHILD_PID}"
    _SIG_EXIT=$?
    "${PYTHON}" "${LIFECYCLE}" exit notifier "${_SIG_EXIT}" || true
    exit "${_SIG_EXIT}"
}
trap '_on_signal' TERM INT

# ── Wait for normal exit ──────────────────────────────────────────────────────
wait "${CHILD_PID}"
EXIT_CODE=$?

# ── Record exit (normal path — not via signal) ────────────────────────────────
"${PYTHON}" "${LIFECYCLE}" exit notifier "${EXIT_CODE}" || true

exit "${EXIT_CODE}"
