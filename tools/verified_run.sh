#!/bin/bash
# verified_run.sh
#
# Usage: ./verified_run.sh "<command to run>"
#
# Wraps a command execution in a tamper-evident, append-only hash chain.
# Each log entry's hash depends on the previous entry's hash, so altering
# or deleting any past entry breaks the chain from that point forward.
#
# This does NOT prove the command was run by an honest process — an agent
# with filesystem access could still choose not to use this script and
# fabricate a log by hand. What it DOES do: make it much harder to quietly
# edit or backdate a SINGLE entry after the fact without the whole chain
# visibly breaking, and it gives Joel a mechanical, non-narrative way to
# check consistency himself or hand to Claude to check.

set -euo pipefail

LOG_FILE="${VERIFIED_LOG_FILE:-./evidence_chain.log}"
CMD="$1"

if [ -z "$CMD" ]; then
  echo "Usage: $0 \"<command>\"" >&2
  exit 1
fi

# Acquire exclusive lock for the entire script so concurrent invocations
# serialize on PREV_HASH/SEQ read and log append. Without this lock two
# concurrent calls read the same PREV_HASH and SEQ, both compute seq=N,
# and the second writer produces a duplicate entry that breaks the chain.
# FD 9 is reserved for the lock; do not use it inside commands passed here.
LOCK_FILE="${LOG_FILE}.lock"
exec 9>"$LOCK_FILE"
flock -x 9

# Get previous hash (or genesis value if log is empty/doesn't exist)
if [ -f "$LOG_FILE" ] && [ -s "$LOG_FILE" ]; then
  PREV_HASH=$(tail -n 1 "$LOG_FILE" | python3 -c "import sys,json; print(json.load(sys.stdin)['entry_hash'])")
else
  PREV_HASH="GENESIS_0000000000000000000000000000000000000000000000000000000000"
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")
if [ -f "$LOG_FILE" ]; then
  SEQ=$(( $(wc -l < "$LOG_FILE") + 1 ))
else
  SEQ=1
fi

# DPL pre-seal: update engine_integrity_refs.json commit_sha to live HEAD NOW,
# before eval "$CMD" and before any other file write in this invocation.
# This is the FIRST write in the sealed run — no write may precede it.
# Any commit that landed before this line executes is captured; any commit
# that lands after does not affect this run's refs.json attribution.
_SCRIPT_DIR_PRE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DPL_REFS_PRE="${_SCRIPT_DIR_PRE}/../artifacts/stock-scanner-api/dpl/engine_integrity_refs.json"
_DPL_PRESEAL="${_SCRIPT_DIR_PRE}/../artifacts/stock-scanner-api/tools/pre_seal_update_refs.sh"
if [ -f "${_DPL_REFS_PRE}" ] && [ -f "${_DPL_PRESEAL}" ]; then
  bash "${_DPL_PRESEAL}" "${_DPL_REFS_PRE}"
fi

# Seal-freshness check: non-blocking warning if engine_root_hash is stale.
# Runs BEFORE eval "$CMD" so the warning appears before any command output.
# Result is stored in _SEAL_STATUS and written into the chain entry.
_SEAL_STATUS="SEAL_UNKNOWN"
if [ -f "${_DPL_REFS_PRE}" ]; then
  _SEAL_DPL_DIR="$(dirname "${_DPL_REFS_PRE}")"
  _SEAL_STATUS=$(python3 - "${_DPL_REFS_PRE}" "${_SEAL_DPL_DIR}" <<'_SEAL_PY'
import sys
refs_path, dpl_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, dpl_dir)
try:
    from engine_manifest import verify_against_refs
    r = verify_against_refs(refs_path)
    print("SEAL_FRESH" if r.get("ok") else "SEAL_STALE")
except Exception:
    print("SEAL_UNKNOWN")
_SEAL_PY
  ) || _SEAL_STATUS="SEAL_UNKNOWN"
  if [ "${_SEAL_STATUS}" = "SEAL_STALE" ]; then
    echo "WARNING: SEAL_STALE — engine_root_hash mismatch. Re-seal engine_integrity_refs.json before next Phase 3 run." >&2
  fi
fi

# Execute the actual command, capture stdout+stderr combined, and exit code
set +e
OUTPUT=$(eval "$CMD" 2>&1)
EXIT_CODE=$?
set -e

OUTPUT_SHA256=$(printf '%s' "$OUTPUT" | sha256sum | awk '{print $1}')

# Canonical string that gets hashed to produce this entry's hash.
# Order matters and must never change, or old entries become unverifiable.
CANONICAL="${PREV_HASH}|${SEQ}|${TIMESTAMP}|${CMD}|${EXIT_CODE}|${OUTPUT_SHA256}"
ENTRY_HASH=$(printf '%s' "$CANONICAL" | sha256sum | awk '{print $1}')

# Write the log entry as a single JSON line (append-only)
python3 -c "
import json
entry = {
    'seq': $SEQ,
    'timestamp_utc': '$TIMESTAMP',
    'command': '''$CMD''',
    'exit_code': $EXIT_CODE,
    'output_sha256': '$OUTPUT_SHA256',
    'prev_hash': '$PREV_HASH',
    'entry_hash': '$ENTRY_HASH'
}
print(json.dumps(entry))
" >> "$LOG_FILE"

# Also persist full raw output alongside, keyed by seq + output hash,
# so the output can be inspected later without re-running the command.
RAW_DIR="${LOG_FILE%.log}_raw"
mkdir -p "$RAW_DIR"
printf '%s' "$OUTPUT" > "$RAW_DIR/${SEQ}_${OUTPUT_SHA256:0:12}.txt"

# DPL verified_run_chain.jsonl write + archive log creation (Option A fix).
# The original artifacts/stock-scanner-api/tools/verified_run.sh that maintained
# this chain no longer exists.  This block restores the write path.
# C33 canonical format: sha256 of all entry fields except
# {entry_hash, type, pre_chain_anchor_note, archive_sha256}.
# archive_sha256 binds the chain entry to a per-SEQ archive log and to the
# index TSV (3-way binding checked by PSV1/2/4/7/8/9 and C44).
_DPL_SDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DPL_CHAIN_W="${_DPL_SDIR}/../artifacts/stock-scanner-api/tools/verified_run_chain.jsonl"
_DPL_LRR_W="${_DPL_SDIR}/../artifacts/stock-scanner-api/tools/last_run_results.json"
_DPL_VER_W="${_DPL_SDIR}/../artifacts/stock-scanner-api/dpl/verify_dpl_phase3.py"
_DPL_REFS_W="${_DPL_SDIR}/../artifacts/stock-scanner-api/dpl/engine_integrity_refs.json"
_DPL_LOGS_W="${_DPL_SDIR}/../artifacts/stock-scanner-api/tools/logs"
_DPL_IDX_W="${_DPL_LOGS_W}/verified_run_index.tsv"
_DPL_OUTPUT_TMP_W="/tmp/verified_run_output_$$.txt"
printf '%s' "$OUTPUT" > "${_DPL_OUTPUT_TMP_W}"
if [ -f "${_DPL_CHAIN_W}" ]; then
python3 -c "
import json, os, hashlib, datetime, subprocess

chain_path    = '${_DPL_CHAIN_W}'
lrr_path      = '${_DPL_LRR_W}'
ver_path      = '${_DPL_VER_W}'
refs_path     = '${_DPL_REFS_W}'
logs_dir      = '${_DPL_LOGS_W}'
index_path    = '${_DPL_IDX_W}'
output_tmp    = '${_DPL_OUTPUT_TMP_W}'
cmd           = '''${CMD}'''
exit_code     = ${EXIT_CODE}
ts            = '${TIMESTAMP}'
output_sha256 = '${OUTPUT_SHA256}'

with open(chain_path) as f:
    entries = [json.loads(l) for l in f if l.strip()]
next_seq  = max(e['seq'] for e in entries) + 1
prev_hash = entries[-1]['entry_hash']

git_root = os.path.dirname(os.path.dirname(chain_path))
commit = subprocess.run(
    ['git', '--no-optional-locks', 'rev-parse', 'HEAD'],
    capture_output=True, text=True, cwd=git_root).stdout.strip()
tree_out = subprocess.run(
    ['git', '--no-optional-locks', 'status', '--porcelain'],
    capture_output=True, text=True, cwd=git_root).stdout.strip()
tree = 'DIRTY' if tree_out else 'CLEAN'

def sha256f(p):
    try:    return hashlib.sha256(open(p, 'rb').read()).hexdigest()
    except: return 'MISSING'

ts_end = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

# Read raw command output for archive file
try:
    with open(output_tmp, 'rb') as f:
        raw_output_bytes = f.read()
except Exception:
    raw_output_bytes = b''

# Build archive file content.
# Header lines satisfy PSV7 (SEQ=N  EXIT=N) and PSV9 (CMD=...).
# Raw output satisfies PSV8 (SUMMARY: line present for verifier runs).
# Archive is written BEFORE the chain entry so archive_sha256 can be
# included in the chain entry (3-way binding: chain <-> file <-> index).
os.makedirs(logs_dir, exist_ok=True)
archive_path = os.path.join(logs_dir, f'verified_run_{next_seq}.log')
header = f'SEQ={next_seq}  EXIT={exit_code}  TS_END={ts_end}\nCMD={cmd}\n'.encode('utf-8')
archive_bytes = header + raw_output_bytes
with open(archive_path, 'wb') as f:
    f.write(archive_bytes)
os.chmod(archive_path, 0o444)
archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
print(f'[dpl_chain] archive=verified_run_{next_seq}.log  archive_sha256={archive_sha256[:16]}...')

entry = {
    'archive_sha256':          archive_sha256,
    'cmd':                     cmd,
    'commit':                  commit,
    'exit_code':               exit_code,
    'last_run_results_sha256': sha256f(lrr_path),
    'log_sha256':              output_sha256,
    'prev_hash':               prev_hash,
    'req6_weights_hash':       sha256f(refs_path),
    'scoring_fn_ast_hash':     sha256f(ver_path),
    'seal_status':             '${_SEAL_STATUS}',
    'seq':                     next_seq,
    'tree':                    tree,
    'ts':                      ts,
    'ts_end':                  ts_end,
}
exclude = {'entry_hash', 'type', 'pre_chain_anchor_note', 'archive_sha256'}
payload = {k: v for k, v in entry.items() if k not in exclude}
entry['entry_hash'] = hashlib.sha256(
    json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
).hexdigest()

# Append to index TSV: SEQ TAB TS_END TAB EXIT_CODE TAB ARCHIVE_SHA256 TAB CMD
# Column 4 (1-indexed) = archive_sha256.  PSV2 reads col-4 with awk field split.
with open(index_path, 'a') as f:
    f.write(f'{next_seq}\t{ts_end}\t{exit_code}\t{archive_sha256}\t{cmd}\n')

with open(chain_path, 'a') as f:
    f.write(json.dumps(entry) + '\n')
print('[dpl_chain] SEQ=' + str(next_seq) + ' entry_hash=' + entry['entry_hash'][:16] + '...')
" 2>&1 || echo "[dpl_chain] ERROR: chain write failed"
else
  echo "[dpl_chain] WARNING: ${_DPL_CHAIN_W} not found — DPL chain write skipped" >&2
fi
rm -f "${_DPL_OUTPUT_TMP_W}"

echo "--- verified_run: entry #$SEQ logged ---"
echo "command:      $CMD"
echo "exit_code:    $EXIT_CODE"
echo "output_sha256: $OUTPUT_SHA256"
echo "entry_hash:   $ENTRY_HASH"

# DPL post-seal verification — runs after each seal when DPL chain files are present.
# post_seal_verify.sh checks 3-way binding (archive sha / index sha / chain sha),
# entry-hash recomputation, and prev-hash chain continuity for the latest sealed entry.
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DPL_CHAIN="${_SCRIPT_DIR}/../artifacts/stock-scanner-api/tools/verified_run_chain.jsonl"
_DPL_PSV="${_SCRIPT_DIR}/../artifacts/stock-scanner-api/tools/post_seal_verify.sh"
if [ -f "${_DPL_CHAIN}" ] && [ -f "${_DPL_PSV}" ]; then
  _DPL_SEQ=$(python3 -c "
import json, sys
entries = [json.loads(l.strip()) for l in open('${_DPL_CHAIN}') if l.strip()]
print(entries[-1]['seq'])
" 2>/dev/null || echo "0")
  _DPL_IDX="${_SCRIPT_DIR}/../artifacts/stock-scanner-api/tools/logs/verified_run_index.tsv"
  _DPL_LOGS="${_SCRIPT_DIR}/../artifacts/stock-scanner-api/tools/logs"
  bash "${_DPL_PSV}" "${_DPL_SEQ}" "${_DPL_CHAIN}" "${_DPL_IDX}" "${_DPL_LOGS}" || true
fi

echo "--- raw output follows ---"
echo "$OUTPUT"
