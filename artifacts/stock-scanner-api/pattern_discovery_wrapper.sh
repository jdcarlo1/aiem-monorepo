#!/usr/bin/env bash
# Wrapper for AIEM pattern-discovery continuous worker (crash-forensics sibling).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIFECYCLE="${SCRIPT_DIR}/crash_forensics_lifecycle.py"
PYTHON="$(command -v python3 2>/dev/null || command -v python)"

"${PYTHON}" "${LIFECYCLE}" start pattern-discovery || true
"${PYTHON}" "${SCRIPT_DIR}/aiem_pattern_discovery_runner.py" &
CHILD_PID=$!
_on_signal() {
    kill -TERM "${CHILD_PID}" 2>/dev/null || true
    wait "${CHILD_PID}"
    _SIG_EXIT=$?
    "${PYTHON}" "${LIFECYCLE}" exit pattern-discovery "${_SIG_EXIT}" || true
    exit "${_SIG_EXIT}"
}
trap _on_signal TERM INT
wait "${CHILD_PID}"
_EXIT=$?
"${PYTHON}" "${LIFECYCLE}" exit pattern-discovery "${_EXIT}" || true
exit "${_EXIT}"
