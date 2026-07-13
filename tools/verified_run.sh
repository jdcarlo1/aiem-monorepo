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

echo "--- verified_run: entry #$SEQ logged ---"
echo "command:      $CMD"
echo "exit_code:    $EXIT_CODE"
echo "output_sha256: $OUTPUT_SHA256"
echo "entry_hash:   $ENTRY_HASH"
echo "--- raw output follows ---"
echo "$OUTPUT"
