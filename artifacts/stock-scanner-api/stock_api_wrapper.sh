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

# ── Run the process ───────────────────────────────────────────────────────────
"${PYTHON}" "${SCRIPT_DIR}/main.py"
EXIT_CODE=$?

# ── Record exit ───────────────────────────────────────────────────────────────
"${PYTHON}" "${LIFECYCLE}" exit stock-api "${EXIT_CODE}" || true

exit "${EXIT_CODE}"
