#!/usr/bin/env python3
"""
issue_tla.py — Issue a Trading Logic Approval (TLA) record.

Usage:
    python3 tools/issue_tla.py [--note "text"] [--approved-by "Joel"]

Reads the currently staged diff for protected trading-logic files,
computes its sha256, writes a new approval record to
tools/trading_logic_approvals.jsonl, and prints the approval_id.

The approval_id is the first 8 hex chars of sha256(diff_sha256 + utc_timestamp).
This ties the ID to both the content and the moment of issuance.

After running this, commit with:
    TLA_APPROVAL_ID=<id> git commit -m "your message"

The pre-commit hook will verify the approval record before allowing
the commit through.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

# Must match trading_logic_gate.sh PROTECTED_PATTERNS exactly.
PROTECTED_PATTERNS = [
    "artifacts/stock-scanner-api/main.py",
    "artifacts/stock-scanner-api/aiem_v3_discovery.py",
    "artifacts/stock-scanner-api/aiem_position_sizing.py",
    "artifacts/stock-scanner-api/aiem_options_pipeline.py",
    "artifacts/stock-scanner-api/aiem_options_scheduler.py",
    "artifacts/stock-scanner-api/aiem_options_dpl.py",
    "artifacts/stock-scanner-api/aiem_strat_engine/scoring.py",
    "artifacts/stock-scanner-api/aiem_strat_scheduler.py",
]

# Glob patterns handled separately (aiem_options_*.py, aiem_paper_*.py)
PROTECTED_GLOB_PREFIXES = [
    ("artifacts/stock-scanner-api/", "aiem_options_"),
    ("artifacts/stock-scanner-api/", "aiem_paper_"),
]


def get_repo_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def get_staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=True
    )
    return [f for f in result.stdout.splitlines() if f]


def is_protected(path: str) -> bool:
    if path in PROTECTED_PATTERNS:
        return True
    for prefix, name_prefix in PROTECTED_GLOB_PREFIXES:
        filename = os.path.basename(path)
        dirpath = os.path.dirname(path) + "/"
        if dirpath == prefix and filename.startswith(name_prefix):
            return True
    return False


def get_staged_diff_sha256(files: list[str]) -> str:
    result = subprocess.run(
        ["git", "diff", "--cached", "--"] + files,
        capture_output=True, check=True
    )
    return hashlib.sha256(result.stdout).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Issue a Trading Logic Approval record")
    parser.add_argument("--note", default="", help="Free-text note (e.g. task/directive ref)")
    parser.add_argument("--approved-by", required=True,
                        help="Approver name — must be passed explicitly; no default accepted")
    args = parser.parse_args()

    repo_root = get_repo_root()
    approvals_path = os.path.join(repo_root, "tools", "trading_logic_approvals.jsonl")

    staged = get_staged_files()
    protected_staged = [f for f in staged if is_protected(f)]

    if not protected_staged:
        print("No protected trading-logic files are currently staged.")
        print("Stage the files you want to commit first (git add ...), then re-run.")
        sys.exit(1)

    print("Protected files staged:")
    for f in protected_staged:
        print(f"  {f}")

    diff_sha256 = get_staged_diff_sha256(protected_staged)
    now_utc = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    # approval_id = first 8 hex chars of sha256(diff_sha256 + timestamp)
    id_input = (diff_sha256 + now_utc).encode()
    approval_id = hashlib.sha256(id_input).hexdigest()[:8]

    # Check for duplicate diff sha256 in existing unused records
    existing = []
    if os.path.exists(approvals_path):
        with open(approvals_path) as f:
            existing = [json.loads(l) for l in f if l.strip()]

    for rec in existing:
        if (rec.get('staged_diff_sha256') == diff_sha256
                and not rec.get('used')
                and rec.get('files_covered') == protected_staged):
            print(f"\nWARNING: an unused approval already exists for this exact diff:")
            print(f"  approval_id={rec['approval_id']}  issued_at={rec['approved_at']}")
            print("Issuing a second one anyway — use the one that matches.")

    record = {
        "approval_id":        approval_id,
        "approved_by":        args.approved_by,
        "approved_at":        now_utc,
        "files_covered":      protected_staged,
        "staged_diff_sha256": diff_sha256,
        "used":               False,
        "used_at":            None,
        "note":               args.note,
    }

    with open(approvals_path, "a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"\nApproval record written to {approvals_path}")
    print(f"\napproval_id:        {approval_id}")
    print(f"approved_by:        {args.approved_by}")
    print(f"approved_at:        {now_utc}")
    print(f"staged_diff_sha256: {diff_sha256}")
    print(f"\nCommit with:")
    print(f"  TLA_APPROVAL_ID={approval_id} git commit -m \"your message\"")


if __name__ == "__main__":
    main()
