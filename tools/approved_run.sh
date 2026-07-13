#!/bin/bash
# approved_run.sh
#
# Usage: ./tools/approved_run.sh "approval reason" "command"
#
# Structural enforcement of the pre-insert approval protocol.
# Any test-data INSERT, UPDATE, or DELETE that touches the evidence chain
# must go through this wrapper, not verified_run.sh directly.
#
# What it does:
#   1. Refuses to run if the approval string is empty — the script exits
#      with an error before logging or executing anything.
#   2. Logs the approval statement as a verified_run.sh entry immediately
#      before the command. This makes it structurally impossible to run
#      the command without a prior approval record in the evidence chain.
#   3. Runs the actual command via verified_run.sh (inherits flock protection).
#
# The two adjacent log entries (PRE_APPROVAL then COMMAND) are linked by the
# hash chain, so any gap between them is detectable.

set -euo pipefail

APPROVAL="${1:-}"
CMD="${2:-}"

if [ -z "$APPROVAL" ]; then
  echo "ERROR: approved_run.sh requires a non-empty approval statement as first argument." >&2
  echo "Usage: $0 \"approval reason\" \"command\"" >&2
  exit 1
fi

if [ -z "$CMD" ]; then
  echo "ERROR: approved_run.sh requires a command as second argument." >&2
  echo "Usage: $0 \"approval reason\" \"command\"" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

APPROVAL_TMP=$(mktemp /tmp/approved_run_XXXXXX.txt)
trap "rm -f $APPROVAL_TMP" EXIT
printf 'PRE_APPROVAL_RECORD\nAuthorized: %s\n' "$APPROVAL" > "$APPROVAL_TMP"

"$SCRIPT_DIR/verified_run.sh" "cat $APPROVAL_TMP"
"$SCRIPT_DIR/verified_run.sh" "$CMD"
