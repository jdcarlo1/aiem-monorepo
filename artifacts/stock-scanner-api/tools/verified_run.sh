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
#
# SEQ CHAIN DISCONTINUITY NOTE (recorded 2026-07-19):
#   SEQ is a per-workspace monotonic counter stored in tools/verified_run_seq.
#   It is NOT a global continuous chain across all time.
#   Prior to the R4.1 durability proof (2026-07-19), SEQ was stored in /tmp
#   and reset on every VM restart.  Historical runs cannot be totally ordered
#   by SEQ alone.  Authoritative ordering uses TS_END (UTC) from the run log.
#   Canonical sequence since R4.1: SEQ=3 (2026-07-19T14:51:15Z) onward.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="/tmp/portfolio_engine_verify.lock"
LOG_FILE="${SCRIPT_DIR}/verified_run_last.log"

CMD="${1:-python portfolio_engine_verify.py --section ALL}"

# ── Monotonic sequence number: workspace-durable file (not /tmp) ──────────
# SEQ_FILE lives in SCRIPT_DIR (git-tracked workspace). Survives VM restarts.
# tee on LOG_FILE truncates each run — LOG_FILE cannot hold SEQ state.
SEQ_FILE="${SCRIPT_DIR}/verified_run_seq"
SEQ_TMP="/tmp/portfolio_engine_verify_seq_$$"
(
  flock -x 200
  LAST_SEQ=$(cat "${SEQ_FILE}" 2>/dev/null | tr -d ' \r\n' || echo 0)
  echo "$(( ${LAST_SEQ:-0} + 1 ))" | tee "${SEQ_FILE}" > "${SEQ_TMP}"
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
# ── Working tree state (R8.6) ─────────────────────────────────────────────
GIT_PORCELAIN=$(git --no-optional-locks -C "${GIT_ROOT}" status --porcelain 2>/dev/null || true)
if [ -z "${GIT_PORCELAIN}" ]; then
    echo "TREE=CLEAN"
else
    echo "TREE=DIRTY"
    echo "git_status_porcelain:"
    while IFS= read -r _vl; do echo "  ${_vl}"; done <<< "${GIT_PORCELAIN}"
    echo "sha256_modified_files:"
    while IFS= read -r _vl; do
        _vrel="${_vl:3}"
        _vfp="${GIT_ROOT}/${_vrel}"
        [ -f "${_vfp}" ] && echo "  ${_vrel}=$(sha256sum "${_vfp}" | awk '{print $1}')" || true
    done <<< "${GIT_PORCELAIN}"
fi
# ── Scoring function integrity (R4.9.5) ───────────────────────────────────
# Records sha256(AST(compute_req6_score)) and sha256(_REQ6_SCORING_WEIGHTS)
# so an uncommitted source state is captured in the run log header.
_SCORER_DIR="$(dirname "${SCRIPT_DIR}")"
_SCORING_HASHES=$(python3 - "${_SCORER_DIR}" <<'_PYEOF'
import sys, ast, hashlib, json
_d = sys.argv[1]
sys.path.insert(0, _d)
try:
    from aiem_options_pipeline import compute_req6_score, _REQ6_SCORING_WEIGHTS
    import inspect
    _src  = inspect.getsource(compute_req6_score)
    _ah   = hashlib.sha256(ast.dump(ast.parse(_src)).encode()).hexdigest()
    _wh   = hashlib.sha256(json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True).encode()).hexdigest()
    print(f"scoring_fn_ast_hash={_ah}")
    print(f"req6_weights_hash={_wh}")
except Exception as _e:
    print(f"scoring_fn_ast_hash=ERROR:{_e}")
    print(f"req6_weights_hash=ERROR:{_e}")
_PYEOF
)
echo "${_SCORING_HASHES}"
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
