#!/usr/bin/env bash
# verify_chain.sh — recomputes and validates the full hash chain from scratch
# Usage: ./tools/verify_chain.sh [evidence_chain.log]
#
# For every entry in the log it:
#   - Verifies prev_entry_hash matches the preceding entry's entry_hash
#   - Recomputes output_hash from the saved output file and checks it matches
#   - Recomputes entry_hash from stored fields and checks it matches
#
# Exits 0 and prints CHAIN OK if all checks pass.
# Exits 1 and prints CHAIN BROKEN (with detail) if any check fails.
# Stop immediately if CHAIN BROKEN — do not attempt to silently fix or
# regenerate the log.

set -uo pipefail

LOG_FILE="${1:-$(dirname "${BASH_SOURCE[0]}")/evidence_chain.log}"

if [ ! -f "$LOG_FILE" ]; then
    echo "ERROR: log file not found: $LOG_FILE" >&2
    exit 1
fi

python3 - "$LOG_FILE" <<'PYEOF'
import json, hashlib, sys, os

log_file = sys.argv[1]

with open(log_file) as f:
    lines = [l.strip() for l in f if l.strip()]

if not lines:
    print("LOG EMPTY — nothing to verify")
    sys.exit(0)

GENESIS  = '0' * 64
errors   = []
prev_hash = GENESIS

for i, line in enumerate(lines, 1):
    try:
        entry = json.loads(line)
    except json.JSONDecodeError as e:
        errors.append(f"Line {i}: JSON parse error: {e}")
        continue

    en           = entry.get('entry_num', '?')
    stored_hash  = entry.get('entry_hash', '')
    stored_prev  = entry.get('prev_entry_hash', '')
    command      = entry.get('command', '')
    timestamp    = entry.get('timestamp', '')
    exit_code    = entry.get('exit_code', 0)
    output_file  = entry.get('output_file', '')
    stored_out_h = entry.get('output_hash', '')

    # 1. Check prev_entry_hash linkage
    if stored_prev != prev_hash:
        errors.append(
            f"Entry #{en} (log line {i}): prev_entry_hash mismatch\n"
            f"  stored:   {stored_prev}\n"
            f"  expected: {prev_hash}"
        )

    # 2. Recompute output_hash from the raw output file
    if os.path.isfile(output_file):
        with open(output_file, 'rb') as f:
            raw = f.read()
        computed_out_h = hashlib.sha256(raw).hexdigest()
        if computed_out_h != stored_out_h:
            errors.append(
                f"Entry #{en} (log line {i}): output_hash mismatch "
                f"(output file modified?)\n"
                f"  stored:   {stored_out_h}\n"
                f"  computed: {computed_out_h}"
            )
    else:
        errors.append(
            f"Entry #{en} (log line {i}): output_file not found: {output_file}"
        )

    # 3. Recompute entry_hash from stored fields
    # (use stored_out_h so the chain check is independent of file existence)
    hash_input       = f"{stored_prev}|{timestamp}|{command}|{stored_out_h}|{exit_code}"
    computed_entry_h = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
    if computed_entry_h != stored_hash:
        errors.append(
            f"Entry #{en} (log line {i}): entry_hash mismatch\n"
            f"  stored:   {stored_hash}\n"
            f"  computed: {computed_entry_h}"
        )

    label = 'OK  ' if not errors or all(f'Entry #{en}' not in e for e in errors) else 'FAIL'
    print(f"  #{en:>4}  {timestamp}  {label}  {stored_hash[:16]}...  cmd={command[:55]!r}")

    prev_hash = stored_hash  # advance chain using stored hash (errors stay visible)

print()
print(f"Entries checked:  {len(lines)}")
print(f"Tail entry_hash:  {json.loads(lines[-1])['entry_hash']}")
print()

if errors:
    print("CHAIN BROKEN")
    print()
    for e in errors:
        print(f"  ERROR: {e}")
    sys.exit(1)
else:
    print("CHAIN OK")
    sys.exit(0)
PYEOF
