#!/usr/bin/env bash
# pre_seal_update_refs.sh — Atomically sets engine_integrity_refs.json commit_sha
# to the live git HEAD immediately before the DPL sealed run begins.
#
# Called as the FIRST step of tools/verified_run.sh, before eval "$CMD" and before
# any other file write in that invocation.  This eliminates the window where an
# auto-commit or mid-session commit could advance HEAD past the value approved by
# the reviewer, causing C28_refs_commit_sha_matches_run_head to FAIL.
#
# Proof property: commit_sha in the sealed refs.json == HEAD at the moment the
# verifier starts reading it.  Any commit landing before this script runs will be
# captured; any commit landing after is not attributable to this sealed run anyway.
#
# Usage: bash pre_seal_update_refs.sh <path-to-engine_integrity_refs.json>
# Exit codes: 0 = success, 1 = bad args or file missing, 2 = Python write failed

set -euo pipefail

REFS_PATH="${1:-}"
if [ -z "${REFS_PATH}" ]; then
  echo "[pre_seal] ERROR: refs.json path argument required" >&2
  exit 1
fi
if [ ! -f "${REFS_PATH}" ]; then
  echo "[pre_seal] ERROR: refs.json not found: ${REFS_PATH}" >&2
  exit 1
fi

BEFORE_SHA=$(sha256sum "${REFS_PATH}" | awk '{print $1}')
HEAD_SHA=$(git --no-optional-locks rev-parse HEAD)
NOW_UTC=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

python3 - "${REFS_PATH}" "${HEAD_SHA}" "${NOW_UTC}" << 'PYEOF'
import sys, json, os

refs_path, head_sha, now_utc = sys.argv[1], sys.argv[2], sys.argv[3]

with open(refs_path, 'r') as f:
    refs = json.load(f)

old_sha = refs.get('commit_sha', 'MISSING')
refs['commit_sha'] = head_sha
refs['commit_sha_note'] = (
    "Set atomically by pre_seal_update_refs.sh at seal time "
    "(live git rev-parse HEAD). Previous value: " + old_sha + "."
)
refs['refs_updated_at'] = now_utc

tmp = refs_path + '.preseal.tmp'
with open(tmp, 'w') as f:
    json.dump(refs, f, sort_keys=True, indent=2, ensure_ascii=False)
    f.write('\n')
os.replace(tmp, refs_path)

print("[pre_seal] OK: commit_sha updated")
print(f"[pre_seal] old_sha={old_sha}")
print(f"[pre_seal] new_sha={head_sha}")
PYEOF

AFTER_SHA=$(sha256sum "${REFS_PATH}" | awk '{print $1}')

echo "[pre_seal] HEAD=${HEAD_SHA}"
echo "[pre_seal] refs_path=${REFS_PATH}"
echo "[pre_seal] before_sha256=${BEFORE_SHA}"
echo "[pre_seal] after_sha256=${AFTER_SHA}"
