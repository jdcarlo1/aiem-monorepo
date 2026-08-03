#!/usr/bin/env python3
"""
reinstate_purged_2026-08-03.py — Reinstate the 25 ledger records purged by 3295e360.

Reads records present at 3295e360^ but absent from HEAD, appends them to the
current ledger with three governance annotation keys per record, and reports
before/after line counts and sha256.

DO NOT RUN without Joel's explicit approval at TTY.

Effect:
  - Pure append: +25 records, 0 deletions.
  - Passes Option B (_is_ledger_append_only) with no TLA token required.
  - Each reinstated record carries:
      "reinstated_from":    "3295e360^"
      "self_issued":        True
      "reinstatement_note": "original record purged by 3295e360; reinstated for
                             audit continuity; not a valid authorization"
  - The annotations make clear these records cannot authorize any commit:
    they are forensic evidence only.

Sequencing:
  1. Run this script (python3 tools/tla_patches/reinstate_purged_2026-08-03.py)
  2. Confirm printed counts match expectations (before: N, after: N+25)
  3. git add tools/trading_logic_approvals.jsonl
  4. git commit -m "Reinstate 25 purged records for audit continuity (append-only)"
     (no TLA token in message — ledger-only append passes Option B)
"""

import hashlib
import json
import subprocess
import sys
import os

LEDGER = "tools/trading_logic_approvals.jsonl"
PURGE_COMMIT = "3295e360"


def main() -> None:
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True
    ).stdout.strip()
    ledger_path = os.path.join(repo_root, LEDGER)

    # --- 1. Read HEAD ledger ---
    with open(ledger_path, "r", encoding="utf-8") as f:
        head_lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    head_ids = {json.loads(ln)["approval_id"] for ln in head_lines
                if json.loads(ln).get("approval_id") is not None}
    before_count = len(head_lines)

    # ── Idempotency guard — abort if any target id already reinstated ──────
    pre_purge_ids_check = subprocess.run(
        ["git", "show", f"{PURGE_COMMIT}^:{LEDGER}"],
        capture_output=True, text=True, check=True
    ).stdout
    target_ids = {json.loads(l)["approval_id"]
                  for l in pre_purge_ids_check.splitlines()
                  if l.strip() and json.loads(l).get("approval_id") is not None}
    already_reinstated = [json.loads(l) for l in head_lines
                          if json.loads(l).get("reinstated") is True
                          and json.loads(l).get("approval_id") in target_ids]
    if already_reinstated:
        print(f"Idempotency guard: {len(already_reinstated)} record(s) already "
              f"carry reinstated=True — aborting to prevent duplicates.")
        sys.exit(0)

    # --- 2. Read 3295e360^ ledger ---
    result = subprocess.run(
        ["git", "show", f"{PURGE_COMMIT}^:{LEDGER}"],
        capture_output=True, text=True, check=True
    )
    pre_purge_lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]

    # --- 3. Select records absent from HEAD ---
    missing = []
    for ln in pre_purge_lines:
        r = json.loads(ln)
        if r.get("approval_id") is None:
            continue
        if r["approval_id"] not in head_ids:
            missing.append(r)

    if not missing:
        print("No missing records found — ledger already complete.")
        sys.exit(0)

    print(f"Records absent from HEAD:  {len(missing)}")
    print(f"Ledger line count (before): {before_count}")

    # --- 4. Append with governance annotations ---
    annotated = []
    for r in missing:
        r["reinstated_from"]    = f"{PURGE_COMMIT}^"
        r["self_issued"]        = True
        r["reinstated"]         = True
        r["reinstatement_note"] = (
            "original record purged by 3295e360; "
            "reinstated for audit continuity; "
            "not a valid authorization"
        )
        annotated.append(r)

    # --- 5. Write (append only) ---
    with open(ledger_path, "a", encoding="utf-8") as f:
        for r in annotated:
            f.write(json.dumps(r) + "\n")

    # --- 6. Report ---
    with open(ledger_path, "rb") as f:
        new_content = f.read()

    new_lines = [ln for ln in new_content.decode("utf-8").splitlines() if ln.strip()]
    after_count = len(new_lines)
    sha256 = hashlib.sha256(new_content).hexdigest()

    print(f"Ledger line count (after):  {after_count}")
    print(f"Records appended:           {len(annotated)}")
    print(f"sha256 of new ledger:       {sha256}")
    print()
    print("Appended approval_ids:")
    for r in annotated:
        print(f"  {r['approval_id']}"
              f"  self_issued={r['self_issued']}"
              f"  used={r.get('used')}"
              f"  files={r.get('files_covered')}")

    print()
    print("Commit this change (Option B — no TLA token required):")
    print("  git add tools/trading_logic_approvals.jsonl")
    print(f"  git commit -m \"Reinstate {len(annotated)} purged records for audit continuity (append-only)\"")


if __name__ == "__main__":
    main()
