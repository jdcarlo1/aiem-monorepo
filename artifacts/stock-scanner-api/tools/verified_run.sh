#!/usr/bin/env bash
# tools/verified_run.sh — DPL verified evidence runner with cryptographic chain + per-SEQ archival.
# Wraps any verifier script with flock, monotonic SEQ, SHA-256 log anchoring,
# cryptographic chain (verified_run_chain.jsonl), and per-SEQ log archival.
#
# Usage (from artifacts/stock-scanner-api/):
#   bash tools/verified_run.sh "python3 dpl/verify_dpl_phase3.py"
#
# Exit codes: inherit from inner command (0 = all PASS, non-zero = FAIL).
#
# CRYPTOGRAPHIC CHAIN (Item 7):
#   Each run appends one JSON line to tools/verified_run_chain.jsonl.
#   entry_hash = sha256(canonical JSON of {seq, ts, ts_end, cmd, exit_code,
#                commit, tree, log_sha256, scoring_fn_ast_hash,
#                req6_weights_hash, prev_hash})
#   JSON: sort_keys=True, separators=(',',':')
#   prev_hash of SEQ=N is entry_hash of SEQ=N-1 (GENESIS for the first entry).
#   Tampering with any entry breaks the chain; verify_chain.sh detects this.
#
# PER-SEQ LOG ARCHIVAL (Item 8):
#   tools/logs/verified_run_<SEQ>.log — full run log (header + output + footer)
#   tools/logs/verified_run_index.tsv — index: SEQ, TS_END, EXIT, LOG_SHA256, CMD
#   Archived logs are made read-only (chmod 444) immediately after writing.
#   Restore proof: sha256sum of restored file must match index entry.
#
# SEQ CHAIN DISCONTINUITY NOTE (recorded 2026-07-19):
#   SEQ is a per-workspace monotonic counter in tools/verified_run_seq.
#   Prior to R4.1 (2026-07-19) SEQ was in /tmp and reset on VM restart.
#   Authoritative ordering uses TS_END (UTC). Canonical chain starts with the
#   GENESIS entry in verified_run_chain.jsonl anchoring SEQ=14 log sha256.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="/tmp/portfolio_engine_verify.lock"
LOG_FILE="${SCRIPT_DIR}/verified_run_last.log"
CHAIN_FILE="${SCRIPT_DIR}/verified_run_chain.jsonl"
LOGS_DIR="${SCRIPT_DIR}/logs"
INDEX_FILE="${LOGS_DIR}/verified_run_index.tsv"

CMD="${1:-python3 dpl/verify_dpl_phase3.py}"

# ── Create logs/ directory (idempotent) ───────────────────────────────────
mkdir -p "${LOGS_DIR}"

# ── Monotonic SEQ (workspace-durable, survives VM restarts) ───────────────
SEQ_FILE="${SCRIPT_DIR}/verified_run_seq"
SEQ_TMP="/tmp/portfolio_engine_verify_seq_$$"
(
  flock -x 200
  LAST_SEQ=$(cat "${SEQ_FILE}" 2>/dev/null | tr -d ' \r\n' || echo 0)
  echo "$(( ${LAST_SEQ:-0} + 1 ))" | tee "${SEQ_FILE}" > "${SEQ_TMP}"
) 200>"${LOCK_FILE}"
SEQ=$(cat "${SEQ_TMP}"); rm -f "${SEQ_TMP}"

# ── Read prev_hash from chain (GENESIS if chain file absent or empty) ──────
PREV_HASH=$(python3 - "${CHAIN_FILE}" <<'_PYEOF'
import sys, json
try:
    lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    last  = json.loads(lines[-1]) if lines else {}
    print(last.get('entry_hash', 'GENESIS'))
except Exception:
    print('GENESIS')
_PYEOF
)

RUN_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── Full-run stdout capture (header + CMD output + footer) ────────────────
FULL_TMP="/tmp/verified_run_full_${SEQ}_$$.log"

{
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
GIT_COMMIT=$(git --no-optional-locks -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)
echo "git_commit=${GIT_COMMIT}"
echo "prev_chain_hash=${PREV_HASH}"

# ── Working tree state ────────────────────────────────────────────────────
GIT_PORCELAIN=$(git --no-optional-locks -C "${GIT_ROOT}" status --porcelain 2>/dev/null || true)
if [ -z "${GIT_PORCELAIN}" ]; then
    TREE_STATUS="CLEAN"
    echo "TREE=CLEAN"
else
    TREE_STATUS="DIRTY"
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

# ── Scoring function integrity (R4.9.5 + engine root hash) ────────────────
_SCORER_DIR="$(dirname "${SCRIPT_DIR}")"
_SCORING_HASHES=$(python3 - "${_SCORER_DIR}" <<'_PYEOF'
import sys, ast, hashlib, json
_d = sys.argv[1]
sys.path.insert(0, _d)
try:
    from aiem_options_pipeline import compute_req6_score, _REQ6_SCORING_WEIGHTS
    import inspect, os
    _src  = inspect.getsource(compute_req6_score)
    _ah   = hashlib.sha256(ast.dump(ast.parse(_src)).encode()).hexdigest()
    _wh   = hashlib.sha256(json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    print(f"scoring_fn_ast_hash={_ah}")
    print(f"req6_weights_hash={_wh}")
    # Engine root hash (canonical manifest)
    try:
        sys.path.insert(0, os.path.join(_d, 'dpl'))
        from engine_manifest import build_manifest
        _m = build_manifest()
        print(f"engine_root_hash={_m['engine_root_hash']}")
    except Exception as _em:
        print(f"engine_root_hash=ERROR:{_em}")
except Exception as _e:
    print(f"scoring_fn_ast_hash=ERROR:{_e}")
    print(f"req6_weights_hash=ERROR:{_e}")
    print(f"engine_root_hash=ERROR:{_e}")
_PYEOF
)
echo "${_SCORING_HASHES}"
echo "=============================="
echo ""

# ── Run the command ────────────────────────────────────────────────────────
set +e
eval "${CMD}" 2>&1 | tee "${LOG_FILE}"
EXIT_CODE=${PIPESTATUS[0]}
set -e

TS_END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
LOG_SHA=$(sha256sum "${LOG_FILE}" | awk '{print $1}')

SCORING_FN_AST_HASH=$(echo "${_SCORING_HASHES}" | grep "^scoring_fn_ast_hash=" | cut -d= -f2-)
REQ6_WEIGHTS_HASH=$(echo "${_SCORING_HASHES}"   | grep "^req6_weights_hash="   | cut -d= -f2-)

# ── Compute chain entry_hash ───────────────────────────────────────────────
ENTRY_HASH=$(python3 - \
    "${SEQ}" "${RUN_TS}" "${TS_END}" "${CMD}" "${EXIT_CODE}" \
    "${GIT_COMMIT}" "${TREE_STATUS:-UNKNOWN}" "${LOG_SHA}" \
    "${SCORING_FN_AST_HASH:-UNKNOWN}" "${REQ6_WEIGHTS_HASH:-UNKNOWN}" \
    "${PREV_HASH}" <<'_PYEOF'
import sys, hashlib, json
seq_n, ts, ts_end, cmd, exit_c, commit, tree, log_sha, sfah, rwh, prev = sys.argv[1:]
payload = {
    "seq":                 int(seq_n),
    "ts":                  ts,
    "ts_end":              ts_end,
    "cmd":                 cmd,
    "exit_code":           int(exit_c),
    "commit":              commit,
    "tree":                tree,
    "log_sha256":          log_sha,
    "scoring_fn_ast_hash": sfah,
    "req6_weights_hash":   rwh,
    "prev_hash":           prev,
}
h = hashlib.sha256(
    json.dumps(payload, sort_keys=True, separators=(',',':')).encode()
).hexdigest()
print(h)
_PYEOF
)

echo ""
echo "=============================="
echo "SEQ=${SEQ}  EXIT=${EXIT_CODE}  TS_END=${TS_END}"
echo "sha256(log)=${LOG_SHA}"
echo "entry_hash=${ENTRY_HASH}"
echo "=============================="

} 2>&1 | tee "${FULL_TMP}"

# Retrieve values computed inside the subshell
EXIT_CODE_OUTER=$(grep "^SEQ=${SEQ}  EXIT=" "${FULL_TMP}" | sed 's/.*EXIT=\([0-9]*\).*/\1/' || echo 1)
LOG_SHA_OUTER=$(grep "^sha256(log)=" "${FULL_TMP}" | cut -d= -f2-)
ENTRY_HASH_OUTER=$(grep "^entry_hash=" "${FULL_TMP}" | cut -d= -f2-)
TS_END_OUTER=$(grep "^SEQ=${SEQ}  EXIT=" "${FULL_TMP}" | sed 's/.*TS_END=\(.*\)/\1/' || echo unknown)
GIT_COMMIT_OUTER=$(grep "^git_commit=" "${FULL_TMP}" | cut -d= -f2-)
TREE_OUTER=$(grep "^TREE=" "${FULL_TMP}" | cut -d= -f2-)
SCORING_FN_AST_OUTER=$(grep "^scoring_fn_ast_hash=" "${FULL_TMP}" | cut -d= -f2-)
REQ6_WEIGHTS_OUTER=$(grep "^req6_weights_hash=" "${FULL_TMP}" | cut -d= -f2-)

# Append chain entry (atomic: temp file + mv)
CHAIN_TMP="${CHAIN_FILE}.${SEQ}.tmp"
python3 - \
    "${SEQ}" "${RUN_TS}" "${TS_END_OUTER:-unknown}" "${CMD}" \
    "${EXIT_CODE_OUTER:-1}" "${GIT_COMMIT_OUTER:-unknown}" \
    "${TREE_OUTER:-UNKNOWN}" "${LOG_SHA_OUTER:-unknown}" \
    "${SCORING_FN_AST_OUTER:-UNKNOWN}" "${REQ6_WEIGHTS_OUTER:-UNKNOWN}" \
    "${PREV_HASH}" "${ENTRY_HASH_OUTER:-UNKNOWN}" > "${CHAIN_TMP}" <<'_PYEOF'
import sys, json
seq_n, ts, ts_end, cmd, exit_c, commit, tree, log_sha, sfah, rwh, prev, entry_hash = sys.argv[1:]
entry = {
    "seq":                 int(seq_n),
    "ts":                  ts,
    "ts_end":              ts_end,
    "cmd":                 cmd,
    "exit_code":           int(exit_c),
    "commit":              commit,
    "tree":                tree,
    "log_sha256":          log_sha,
    "scoring_fn_ast_hash": sfah,
    "req6_weights_hash":   rwh,
    "prev_hash":           prev,
    "entry_hash":          entry_hash,
}
print(json.dumps(entry, sort_keys=True, separators=(',',':')))
_PYEOF
cat "${CHAIN_TMP}" >> "${CHAIN_FILE}"
rm -f "${CHAIN_TMP}"

# Per-SEQ archive (full output = header + CMD output + footer)
SEQ_LOG="${LOGS_DIR}/verified_run_${SEQ}.log"
cp "${FULL_TMP}" "${SEQ_LOG}"
chmod 444 "${SEQ_LOG}"
rm -f "${FULL_TMP}"

# sha256 of the ARCHIVED file (not LOG_FILE which is CMD-only)
# The index records sha256(archive) so restore integrity can be verified by
# recomputing sha256(verified_run_N.log) and comparing to the index entry.
SEQ_LOG_SHA=$(sha256sum "${SEQ_LOG}" | awk '{print $1}')

# Append to index TSV (SEQ, TS_END, EXIT, SEQ_LOG_SHA256, CMD)
printf '%s\t%s\t%s\t%s\t%s\n' \
    "${SEQ}" "${TS_END_OUTER:-unknown}" "${EXIT_CODE_OUTER:-1}" \
    "${SEQ_LOG_SHA}" "${CMD}" >> "${INDEX_FILE}"

exit "${EXIT_CODE_OUTER:-1}"
