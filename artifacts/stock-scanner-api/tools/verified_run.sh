#!/usr/bin/env bash
# tools/verified_run.sh — Portfolio Engine Phase 2 verified evidence runner.
# Wraps portfolio_engine_verify.py with flock, timestamps, and SHA-256 anchors.
# All output is emitted to stdout in raw form (no reformatting).
#
# Usage (from artifacts/stock-scanner-api/):
#   bash tools/verified_run.sh "python portfolio_engine_verify.py --section ALL"
#
# The CMD is executed from whichever directory the caller is in (no cd).
# Exit codes: inherit from inner command (0=all PASS, non-zero=FAIL).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="/tmp/portfolio_engine_verify.lock"
LOG_FILE="${SCRIPT_DIR}/verified_run_last.log"

CMD="${1:-python portfolio_engine_verify.py --section ALL}"

# ── Monotonic sequence number: derived from last run's log (durable) ──────
# SEQ_FILE removed (was /tmp — ephemeral). State lives in LOG_FILE (workspace).
SEQ_TMP="/tmp/portfolio_engine_verify_seq_$$"
(
  flock -x 200
  LAST_SEQ=$(grep -m1 "^SEQ=" "${LOG_FILE}" 2>/dev/null | cut -d= -f2 | tr -d ' \r' || echo 0)
  echo "$(( ${LAST_SEQ:-0} + 1 ))" > "${SEQ_TMP}"
) 200>"${LOCK_FILE}"
SEQ=$(cat "${SEQ_TMP}"); rm -f "${SEQ_TMP}"

RUN_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── Header ────────────────────────────────────────────────────────────────
echo "====== verified_run.sh ======"
echo "SEQ=${SEQ}"
echo "TS=${RUN_TS}"
echo "CMD=${CMD}"
echo "CWD=$(pwd)"
echo "sha256(verified_run.sh)=$(sha256sum "${SCRIPT_DIR}/verified_run.sh" | awk '{print $1}')"
CHAIN_SH="$(cd "${SCRIPT_DIR}/.." && pwd)/verify_chain.sh"
echo "sha256(verify_chain.sh)=$(sha256sum "${CHAIN_SH}" 2>/dev/null | awk '{print $1}' || echo MISSING)"
GIT_ROOT=$(git --no-optional-locks -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || echo unknown)
echo "git_commit=$(git --no-optional-locks -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
echo "=============================="
echo ""

# ── Run the command from CURRENT working directory (caller's CWD) ─────────
set +e
eval "${CMD}" 2>&1 | tee "${LOG_FILE}"
EXIT_CODE=${PIPESTATUS[0]}
set -e

echo ""
echo "=============================="
echo "SEQ=${SEQ}  EXIT=${EXIT_CODE}  TS_END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "sha256(log)=$(sha256sum "${LOG_FILE}" | awk '{print $1}')"
echo "=============================="

exit ${EXIT_CODE}
