#!/usr/bin/env bash
# verified_run.sh — hash-chained, tamper-evident evidence wrapper
# Usage: ./tools/verified_run.sh "your command here"
#
# For every evidence-producing command, run it through this wrapper.
# It will:
#   1. Execute the command exactly as given (via bash -c)
#   2. Capture full stdout+stderr and exit code
#   3. Compute a SHA-256 hash chaining the previous entry's hash
#   4. Append one JSON line to tools/evidence_chain.log (append-only)
#   5. Save raw output to tools/evidence_chain_outputs/<N>.txt
#   6. Print entry metadata + raw output back to the caller
#
# Hash schema: sha256(prev_entry_hash|timestamp|command|output_hash|exit_code)
# where output_hash = sha256(raw output bytes)
# and prev_entry_hash = 64-zero genesis for the first entry.

set -uo pipefail

TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VR_LOG_FILE="$TOOLS_DIR/evidence_chain.log"
export VR_OUTPUT_DIR="$TOOLS_DIR/evidence_chain_outputs"

mkdir -p "$VR_OUTPUT_DIR"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <command>" >&2
    exit 1
fi

export VR_COMMAND="$*"
export VR_TIMESTAMP
VR_TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Determine entry number and previous hash via Python (handles missing/empty log)
VR_META="$(python3 -c "
import json, os, sys
log = os.environ['VR_LOG_FILE']
if os.path.isfile(log):
    with open(log) as f:
        lines = [l.strip() for l in f if l.strip()]
    n = len(lines) + 1
    prev = json.loads(lines[-1])['entry_hash'] if lines else '0' * 64
else:
    n = 1
    prev = '0' * 64
print(n)
print(prev)
")"

export VR_ENTRY_NUM
VR_ENTRY_NUM="$(echo "$VR_META" | head -1)"
export VR_PREV_HASH
VR_PREV_HASH="$(echo "$VR_META" | tail -1)"
export VR_OUTPUT_FILE="${VR_OUTPUT_DIR}/${VR_ENTRY_NUM}.txt"

# Execute the command; capture stdout+stderr together; preserve exit code
set +e
bash -c "$VR_COMMAND" >"$VR_OUTPUT_FILE" 2>&1
export VR_EXIT_CODE=$?
set -e

# Compute hashes, write log entry, and print result — all in Python so JSON
# serialisation is safe regardless of special characters in the command string.
python3 -c "
import json, hashlib, os

command     = os.environ['VR_COMMAND']
timestamp   = os.environ['VR_TIMESTAMP']
entry_num   = int(os.environ['VR_ENTRY_NUM'])
prev_hash   = os.environ['VR_PREV_HASH']
exit_code   = int(os.environ['VR_EXIT_CODE'])
output_file = os.environ['VR_OUTPUT_FILE']
log_file    = os.environ['VR_LOG_FILE']

with open(output_file, 'rb') as f:
    raw = f.read()
output_hash = hashlib.sha256(raw).hexdigest()

hash_input  = f'{prev_hash}|{timestamp}|{command}|{output_hash}|{exit_code}'
entry_hash  = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()

entry = {
    'entry_num':         entry_num,
    'timestamp':         timestamp,
    'command':           command,
    'exit_code':         exit_code,
    'output_file':       output_file,
    'output_hash':       output_hash,
    'prev_entry_hash':   prev_hash,
    'hash_input_schema': 'sha256(prev_entry_hash|timestamp|command|output_hash|exit_code)',
    'entry_hash':        entry_hash,
}

with open(log_file, 'a') as f:
    f.write(json.dumps(entry) + '\n')

with open(output_file) as f:
    output = f.read()

print(f'=== verified_run entry #{entry_num} ===')
print(f'timestamp:        {timestamp}')
print(f'command:          {command}')
print(f'exit_code:        {exit_code}')
print(f'output_hash:      {output_hash}')
print(f'prev_entry_hash:  {prev_hash}')
print(f'entry_hash:       {entry_hash}')
print('=== raw output ===')
print(output, end='')
print(f'=== end entry #{entry_num} ===')
"
