#!/bin/bash
# verify_chain.sh
#
# Usage: ./verify_chain.sh [path/to/evidence_chain.log]
#
# Independently re-verifies every entry in the hash chain by recomputing
# each entry_hash from its own fields and the previous entry's hash, and
# comparing it to what's stored. Run this yourself (or paste the log to
# Claude and ask it to check) — do NOT rely on the agent to tell you the
# chain is valid; that defeats the purpose.
#
# Implementation: single Python process reads all entries in one pass.
# The hash algorithm and canonical format are identical to verified_run.sh.
#
# KNOWN_BREAKS: if a seq's mismatch is already explained and documented,
# it is listed in tools/KNOWN_BREAKS.json alongside this script.  Such
# entries are printed as KNOWN-BREAK (with reason + record pointer) rather
# than FAIL, and do not halt verification — the chain continues from the
# stored entry_hash.  Any seq NOT in KNOWN_BREAKS.json that fails still
# hard-fails as before.  The allow-list must never be used to suppress a
# new, unexplained break.

set -euo pipefail

LOG_FILE="${1:-./evidence_chain.jsonl}"
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_KB_FILE="${_SCRIPT_DIR}/KNOWN_BREAKS.json"

if [ ! -f "$LOG_FILE" ]; then
  echo "Log file not found: $LOG_FILE" >&2
  exit 1
fi

python3 - "$LOG_FILE" "$_KB_FILE" << 'PYEOF'
import sys, json, hashlib, os

log_file = sys.argv[1]
kb_file  = sys.argv[2]

# Load known-break allow-list (keyed by seq int)
known_breaks = {}
if os.path.isfile(kb_file):
    try:
        data = json.loads(open(kb_file).read())
        for kb in data.get("known_breaks", []):
            known_breaks[kb["seq"]] = kb
    except Exception as e:
        print(f"WARNING: could not load {kb_file}: {e}", file=sys.stderr)

prev_hash = "GENESIS_0000000000000000000000000000000000000000000000000000000000"
fail = False
break_count = 0
known_break_count = 0
line_num = 0

with open(log_file) as f:
    for raw in f:
        raw = raw.strip()
        if not raw:
            continue
        line_num += 1
        e = json.loads(raw)
        seq           = e['seq']
        timestamp     = e['timestamp_utc']
        cmd           = e['command']
        exit_code     = e['exit_code']
        output_sha256 = e['output_sha256']
        stored_prev   = e['prev_hash']
        stored_hash   = e['entry_hash']

        if stored_prev != prev_hash:
            break_count += 1
            print(f"BREAK at line {line_num} (seq={seq}): prev_hash mismatch — chain continuity gap.")
            print(f"  expected prev_hash: {prev_hash}")
            print(f"  stored  prev_hash:  {stored_prev}")
            print("  Cause: UNKNOWN — prev_hash mismatch detected. Do not assume this is benign.")
            canonical_brk = f"{stored_prev}|{seq}|{timestamp}|{cmd}|{exit_code}|{output_sha256}"
            recomputed_brk = hashlib.sha256(canonical_brk.encode()).hexdigest()
            if recomputed_brk == stored_hash:
                print("  Entry is internally valid. Restarting sub-chain from this entry.")
                prev_hash = stored_hash
                continue
            else:
                print("  FATAL: break entry is also internally invalid — possible tampering.")
                fail = True
                break

        canonical   = f"{prev_hash}|{seq}|{timestamp}|{cmd}|{exit_code}|{output_sha256}"
        recomputed  = hashlib.sha256(canonical.encode()).hexdigest()

        if recomputed != stored_hash:
            if seq in known_breaks:
                kb = known_breaks[seq]
                known_break_count += 1
                print(f"KNOWN-BREAK  seq={seq}  entry_hash={stored_hash[:16]}...  (explained, not new tampering)")
                print(f"  reason: {kb['reason']}")
                print(f"  record: {kb['record']}")
                # Advance the chain using the stored (broken) entry_hash, same as
                # subsequent entries' prev_hash values expect.
                prev_hash = stored_hash
                continue
            else:
                print(f"FAIL at line {line_num} (seq={seq}): entry_hash does not match recomputed hash.")
                print("  This entry's fields were altered after being logged, OR the log was hand-edited.")
                print(f"  stored entry_hash:     {stored_hash}")
                print(f"  recomputed entry_hash: {recomputed}")
                fail = True
                break

        print(f"OK  seq={seq}  entry_hash={stored_hash[:16]}...  cmd: {cmd}")
        prev_hash = stored_hash

print()
if not fail:
    if known_break_count > 0 and break_count == 0:
        print(f"=== CHAIN INTACT with {known_break_count} documented known break(s) — see KNOWN-BREAK line(s) above. ===")
        print(f"    Known breaks are explained and listed in tools/KNOWN_BREAKS.json.")
        print(f"    All other entries verified. No new or unexplained tampering detected.")
    elif break_count > 0:
        print(f"=== CHAIN HAS {break_count} UNRESOLVED BREAK(S) — awaiting manual review. Not auto-approved. ===")
        print("    Each break entry above was internally valid but the chain continuity gap is unverified.")
        print("    Cause is UNKNOWN. Do not assume benign. Manual review and explicit approval required.")
        sys.exit(2)
    else:
        print(f"=== CHAIN VALID: all {line_num} entries verified, no tampering detected in the log structure. ===")
    print("NOTE: this confirms internal consistency of the log only. It does NOT prove the")
    print("commands were actually executed as claimed, or that Joel is running this against")
    print("his real production database. Spot-check by re-running a sampled command yourself.")
else:
    print(f"=== CHAIN BROKEN at line {line_num}. The log is not trustworthy past this point. ===")
    sys.exit(1)
PYEOF
