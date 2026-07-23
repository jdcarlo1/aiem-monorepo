#!/bin/bash
# verified_run_pe.sh
#
# Usage: ./tools/verified_run_pe.sh "<command to run>"
#   or:  PE_VERIFIED_LOG_FILE=/path/to/log ./tools/verified_run_pe.sh "<cmd>"
#
# Portfolio Engine evidence chain wrapper — same flock/SEQ/PREV_HASH design
# as tools/verified_run.sh, scoped to aiem_portfolio_engine /
# portfolio_engine_verify.py.  Do NOT modify tools/verified_run.sh — it is
# DPL Phase 3 scope only and must not be repointed.
#
# Each log entry carries:
#   seq, timestamp, command, exit_code, output_sha256, prev_hash, entry_hash,
#   git_commit, git_tree, archive_sha256,
#   pe_config_sha256, pe_verify_sha256, pe_gate_sha256, pe_wrapper_sha256.
#
# CANONICAL string (must never change — changing it makes old entries
# unverifiable):
#   PREV_HASH|SEQ|TIMESTAMP|CMD|EXIT_CODE|OUTPUT_SHA256|GIT_COMMIT|GIT_TREE
#
# Same caveat as verified_run.sh: an agent with filesystem access could choose
# not to use this script.  What it prevents: quiet single-entry edits after the
# fact without breaking the visible chain from that point forward.

set -euo pipefail

# ── Resolve script location first so LOG_FILE default is absolute ─────────
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_GIT_ROOT="$(cd "${_SCRIPT_DIR}/.." && pwd)"
_PE_API_DIR="${_GIT_ROOT}/artifacts/stock-scanner-api"
_PE_CONFIG="${_PE_API_DIR}/aiem_portfolio_engine/config.py"
_PE_VERIFY="${_PE_API_DIR}/portfolio_engine_verify.py"
_PE_GATE="${_PE_API_DIR}/aiem_portfolio_engine/gate.py"
_THIS_SCRIPT="${_SCRIPT_DIR}/verified_run_pe.sh"

LOG_FILE="${PE_VERIFIED_LOG_FILE:-${_PE_API_DIR}/evidence_chain_pe.log}"

if [ $# -lt 1 ] || [ -z "$1" ]; then
  echo "Usage: $0 \"<command to run>\"" >&2
  exit 1
fi
CMD="$1"

# ── Exclusive lock — same FD 9 convention as verified_run.sh ─────────────
LOCK_FILE="${LOG_FILE}.lock"
exec 9>"$LOCK_FILE"
flock -x 9

# ── SEQ + PREV_HASH from last log line ────────────────────────────────────
if [ -f "$LOG_FILE" ] && [ -s "$LOG_FILE" ]; then
  PREV_HASH=$(tail -n 1 "$LOG_FILE" | python3 -c "import sys,json; print(json.load(sys.stdin)['entry_hash'])")
  SEQ=$(( $(wc -l < "$LOG_FILE") + 1 ))
else
  PREV_HASH="GENESIS_PE_000000000000000000000000000000000000000000000000000000"
  SEQ=1
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")

# ── Snapshot PE file hashes + git state BEFORE running the command ────────
sha256_file() { [ -f "$1" ] && sha256sum "$1" | awk '{print $1}' || echo "MISSING"; }
PE_CONFIG_SHA=$(sha256_file "${_PE_CONFIG}")
PE_VERIFY_SHA=$(sha256_file "${_PE_VERIFY}")
PE_GATE_SHA=$(sha256_file "${_PE_GATE}")
PE_WRAPPER_SHA=$(sha256_file "${_THIS_SCRIPT}")

GIT_COMMIT=$(git --no-optional-locks -C "${_GIT_ROOT}" rev-parse HEAD 2>/dev/null || echo "UNKNOWN")
GIT_TREE_OUT=$(git --no-optional-locks -C "${_GIT_ROOT}" status --porcelain 2>/dev/null || echo "")
GIT_TREE=$( [ -z "$GIT_TREE_OUT" ] && echo "CLEAN" || echo "DIRTY" )

# Capture diff stat to a temp file (may contain newlines/special chars)
GIT_DIFF_TMP=$(mktemp)
git --no-optional-locks -C "${_GIT_ROOT}" diff HEAD --stat 2>/dev/null > "$GIT_DIFF_TMP" || true

# ── Execute command ───────────────────────────────────────────────────────
set +e
OUTPUT=$(eval "$CMD" 2>&1)
EXIT_CODE=$?
set -e

TS_END=$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")
OUTPUT_SHA256=$(printf '%s' "$OUTPUT" | sha256sum | awk '{print $1}')

# ── Hash chain ────────────────────────────────────────────────────────────
CANONICAL="${PREV_HASH}|${SEQ}|${TIMESTAMP}|${CMD}|${EXIT_CODE}|${OUTPUT_SHA256}|${GIT_COMMIT}|${GIT_TREE}"
ENTRY_HASH=$(printf '%s' "$CANONICAL" | sha256sum | awk '{print $1}')

# ── Archive raw output (read-only, keyed by SEQ + output_sha256[:12]) ────
RAW_DIR="${LOG_FILE%.log}_raw"
mkdir -p "$RAW_DIR"
ARCHIVE_PATH="${RAW_DIR}/pe_run_${SEQ}_${OUTPUT_SHA256:0:12}.txt"
{
  printf 'SEQ=%s  EXIT=%s  TS_START=%s  TS_END=%s\n' "$SEQ" "$EXIT_CODE" "$TIMESTAMP" "$TS_END"
  printf 'CMD=%s\n' "$CMD"
  printf 'GIT_COMMIT=%s  TREE=%s\n' "$GIT_COMMIT" "$GIT_TREE"
  printf '\n'
  printf '%s' "$OUTPUT"
} > "$ARCHIVE_PATH"
chmod 444 "$ARCHIVE_PATH"
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')

# ── Write log entry (CMD via temp file to avoid quoting issues) ───────────
CMD_TMP=$(mktemp)
printf '%s' "$CMD" > "$CMD_TMP"
DIFF_STAT_CONTENT=$(cat "$GIT_DIFF_TMP")
python3 - << PYEOF >> "$LOG_FILE"
import json, sys
cmd      = open('${CMD_TMP}').read()
diff_stat = open('${GIT_DIFF_TMP}').read().strip()
entry = {
    'seq':              ${SEQ},
    'timestamp_utc':    '${TIMESTAMP}',
    'ts_end':           '${TS_END}',
    'command':          cmd,
    'exit_code':        ${EXIT_CODE},
    'output_sha256':    '${OUTPUT_SHA256}',
    'archive_sha256':   '${ARCHIVE_SHA256}',
    'prev_hash':        '${PREV_HASH}',
    'entry_hash':       '${ENTRY_HASH}',
    'git_commit':       '${GIT_COMMIT}',
    'git_tree':         '${GIT_TREE}',
    'git_diff_stat':    diff_stat or '(clean)',
    'pe_config_sha256': '${PE_CONFIG_SHA}',
    'pe_verify_sha256': '${PE_VERIFY_SHA}',
    'pe_gate_sha256':   '${PE_GATE_SHA}',
    'pe_wrapper_sha256':'${PE_WRAPPER_SHA}',
}
print(json.dumps(entry))
PYEOF
rm -f "$CMD_TMP" "$GIT_DIFF_TMP"

# ── Header block printed to stdout ────────────────────────────────────────
echo "========================================================================"
echo "verified_run_pe  SEQ=${SEQ}"
echo "  timestamp:        ${TIMESTAMP}"
echo "  ts_end:           ${TS_END}"
echo "  exit_code:        ${EXIT_CODE}"
echo "  git_commit:       ${GIT_COMMIT}"
echo "  git_tree:         ${GIT_TREE}"
echo "  output_sha256:    ${OUTPUT_SHA256}"
echo "  archive_sha256:   ${ARCHIVE_SHA256}"
echo "  entry_hash:       ${ENTRY_HASH}"
echo "  prev_hash:        ${PREV_HASH}"
echo "  pe_config_sha256: ${PE_CONFIG_SHA}"
echo "  pe_verify_sha256: ${PE_VERIFY_SHA}"
echo "  pe_wrapper_sha256:${PE_WRAPPER_SHA}"
echo "  command:          ${CMD}"
echo "========================================================================"
echo "--- raw output follows ---"
echo "$OUTPUT"
echo "--- end raw output ---"
