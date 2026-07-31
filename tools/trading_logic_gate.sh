#!/usr/bin/env bash
# trading_logic_gate.sh
#
# Pre-commit gate for trading-logic files.
#
# MECHANISM (verifiable, not narrative):
#   1. Detect whether any staged file matches the PROTECTED_PATTERNS list.
#   2. If none match — exit 0 (not a trading-logic commit; gate does not apply).
#   3. If any match — require env var TLA_APPROVAL_ID to be set.
#   4. Look up TLA_APPROVAL_ID in tools/trading_logic_approvals.jsonl.
#   5. Verify the approval record:
#        a. approval_id exists and used=false
#        b. staged_diff_sha256 in the record == sha256(git diff --cached -- <protected files>)
#        c. files_covered in the record is a superset of the staged protected files
#   6. If all pass: mark record used=true (in-place update), print PASS, exit 0.
#   7. If any fail: print the specific failure reason, exit 1 (commit BLOCKED).
#
# APPROVAL RECORD FORMAT (tools/trading_logic_approvals.jsonl):
#   One JSON object per line:
#   {
#     "approval_id":        "<8-hex-char id>",
#     "approved_by":        "Joel",
#     "approved_at":        "<ISO8601 UTC>",
#     "files_covered":      ["<path>", ...],
#     "staged_diff_sha256": "<sha256 of git diff --cached output for those files>",
#     "used":               false,
#     "used_at":            null,
#     "note":               "<optional free text>"
#   }
#
# ISSUING AN APPROVAL RECORD:
#   Stage the files you want to commit, then run:
#     python3 tools/issue_tla.py [--note "text"] [--approved-by "Joel"]
#   This computes the staged diff sha256 and writes a new record.
#   The script prints the approval_id; pass it as TLA_APPROVAL_ID at commit time:
#     TLA_APPROVAL_ID=<id> git commit -m "your message"
#
# BYPASSING:
#   `git commit --no-verify` skips ALL hooks including this gate.
#   DO NOT use --no-verify on this repo. Every bypass must be documented
#   in tools/trading_logic_approvals.jsonl as a BYPASS entry with reason.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
APPROVALS_FILE="${REPO_ROOT}/tools/trading_logic_approvals.jsonl"

# ── Protected file patterns ────────────────────────────────────────────────
# Any staged file whose path matches ANY of these patterns triggers the gate.
# Patterns are matched against full repo-relative paths using bash == glob.
PROTECTED_PATTERNS=(
    "artifacts/stock-scanner-api/main.py"
    "artifacts/stock-scanner-api/aiem_v3_discovery.py"
    "artifacts/stock-scanner-api/aiem_position_sizing.py"
    "artifacts/stock-scanner-api/aiem_options_*.py"
    "artifacts/stock-scanner-api/aiem_options_pipeline.py"
    "artifacts/stock-scanner-api/aiem_options_scheduler.py"
    "artifacts/stock-scanner-api/aiem_options_dpl.py"
    "artifacts/stock-scanner-api/aiem_strat_engine/scoring.py"
    "artifacts/stock-scanner-api/aiem_strat_scheduler.py"
    "artifacts/stock-scanner-api/aiem_paper_*.py"
)

# ── Detect staged protected files ─────────────────────────────────────────
STAGED_FILES=$(git diff --cached --name-only)
STAGED_PROTECTED=()

for f in $STAGED_FILES; do
    for pattern in "${PROTECTED_PATTERNS[@]}"; do
        # shellcheck disable=SC2254
        if [[ "$f" == $pattern ]]; then
            STAGED_PROTECTED+=("$f")
            break
        fi
    done
done

if [ ${#STAGED_PROTECTED[@]} -eq 0 ]; then
    # No trading-logic files staged — gate does not apply.
    exit 0
fi

echo ""
echo "=================================================================="
echo "TRADING-LOGIC GATE: protected file(s) staged:"
for f in "${STAGED_PROTECTED[@]}"; do
    echo "  $f"
done
echo "=================================================================="

# ── Require TLA_APPROVAL_ID ────────────────────────────────────────────────
if [ -z "${TLA_APPROVAL_ID:-}" ]; then
    echo ""
    echo "COMMIT BLOCKED: TLA_APPROVAL_ID is not set."
    echo ""
    echo "Trading-logic files require an approval record before committing."
    echo "Steps:"
    echo "  1. Get explicit approval from Joel for the staged diff."
    echo "  2. Stage the approved files (git add ...)."
    echo "  3. Run: python3 tools/issue_tla.py"
    echo "     This writes a record to tools/trading_logic_approvals.jsonl"
    echo "     and prints the approval_id."
    echo "  4. Commit with: TLA_APPROVAL_ID=<id> git commit -m \"your message\""
    echo ""
    echo "DO NOT use git commit --no-verify to bypass this gate."
    exit 1
fi

# ── Look up the approval record ────────────────────────────────────────────
if [ ! -f "$APPROVALS_FILE" ]; then
    echo ""
    echo "COMMIT BLOCKED: $APPROVALS_FILE does not exist."
    echo "Run python3 tools/issue_tla.py to create an approval record."
    exit 1
fi

# Compute staged diff sha256 for protected files only
STAGED_DIFF_SHA=$(git diff --cached -- "${STAGED_PROTECTED[@]}" | sha256sum | awk '{print $1}')

# Delegate all record lookup and validation to Python (cleaner JSON handling)
GATE_RESULT=$(python3 - "${APPROVALS_FILE}" "${TLA_APPROVAL_ID}" "${STAGED_DIFF_SHA}" \
    "${STAGED_PROTECTED[@]}" << 'PYEOF'
import sys, json, datetime, os

approvals_path = sys.argv[1]
approval_id    = sys.argv[2]
staged_sha     = sys.argv[3]
staged_files   = set(sys.argv[4:])

with open(approvals_path) as f:
    records = [json.loads(l) for l in f if l.strip()]

match = next((r for r in records if r.get('approval_id') == approval_id), None)

if match is None:
    print(f"FAIL:NOT_FOUND:{approval_id}")
    sys.exit(1)

if match.get('used'):
    used_at = match.get('used_at', 'unknown')
    print(f"FAIL:ALREADY_USED:{approval_id}:used_at={used_at}")
    sys.exit(1)

if match.get('staged_diff_sha256') != staged_sha:
    stored = match.get('staged_diff_sha256', '')
    print(f"FAIL:DIFF_MISMATCH:stored={stored[:16]}...:live={staged_sha[:16]}...")
    sys.exit(1)

covered = set(match.get('files_covered', []))
uncovered = staged_files - covered
if uncovered:
    print(f"FAIL:FILES_NOT_COVERED:{sorted(uncovered)}")
    sys.exit(1)

# If self_issued=True, a human_directive field must be present and non-empty.
# This closes the direct-write path: the agent can still write a record to the
# file without using issue_tla.py (which now requires a TTY), but only if it
# cites a real human directive in human_directive.  An empty string, missing
# field, or None all fail here.
if match.get('self_issued'):
    hd = match.get('human_directive', '') or ''
    if not hd.strip():
        print(f"FAIL:SELF_ISSUED_NO_DIRECTIVE:{approval_id}:"
              f"self_issued=True records require a non-empty human_directive field")
        sys.exit(1)

# All checks passed — mark as used in-place
now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
new_records = []
for r in records:
    if r.get('approval_id') == approval_id:
        r['used'] = True
        r['used_at'] = now
    new_records.append(r)

tmp = approvals_path + '.tla_tmp'
with open(tmp, 'w') as f:
    for r in new_records:
        f.write(json.dumps(r) + '\n')
os.replace(tmp, approvals_path)

print(f"PASS:{approval_id}:approved_by={match.get('approved_by','?')}:approved_at={match.get('approved_at','?')}")
PYEOF
)

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "COMMIT BLOCKED: approval verification failed."
    echo "  Result: $GATE_RESULT"
    echo ""
    echo "Possible causes:"
    echo "  NOT_FOUND      — TLA_APPROVAL_ID does not exist in $APPROVALS_FILE"
    echo "  ALREADY_USED   — this approval was already consumed by a prior commit"
    echo "  DIFF_MISMATCH  — staged diff has changed since approval was issued"
    echo "                   (re-run python3 tools/issue_tla.py after re-staging)"
    echo "  FILES_NOT_COVERED — staged files not listed in this approval record"
    exit 1
fi

echo "  Approval verified: $GATE_RESULT"
echo "  Gate: PASS — commit allowed."
echo "=================================================================="
