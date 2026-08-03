#!/usr/bin/env python3
"""
check_protected_push.py — standalone pre-push TLA gate.

Single source of truth for protected-file push enforcement.  Both
.git/hooks/pre-push and git_autosync_daemon.py delegate here so the
two paths can never drift apart.

MODES
-----
Hook mode (called by .git/hooks/pre-push — git pipes stdin automatically):
    python3 tools/check_protected_push.py
    stdin line format (git pre-push protocol):
        <local-ref> SP <local-sha1> SP <remote-ref> SP <remote-sha1> LF

Range mode (called by daemon or manual inspection):
    python3 tools/check_protected_push.py --range <remote_sha>..<local_sha>

EXIT CODES
----------
  0 — all commits pass (no protected files, or all have valid used=True TLA)
  1 — at least one commit BLOCKED
  2 — usage / environment error

KNOWN BYPASS
------------
`git push --no-verify` skips pre-push hooks by git design.  This residual
gap is documented and cannot be closed at the hook level.  Any use of
--no-verify must be documented in tools/trading_logic_approvals.jsonl as
a BYPASS entry.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys

# ── Paths ─────────────────────────────────────────────────────────────────────
# REPO_DIR is two levels up from tools/check_protected_push.py
REPO_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVALS_FILE = os.path.join(REPO_DIR, "tools", "trading_logic_approvals.jsonl")

# ── Protected patterns (MUST stay in sync with tools/trading_logic_gate.sh
#    and git_autosync_daemon.py PROTECTED_PATTERNS) ───────────────────────────
PROTECTED_PATTERNS: list[str] = [
    "artifacts/stock-scanner-api/main.py",
    "artifacts/stock-scanner-api/aiem_v3_discovery.py",
    "artifacts/stock-scanner-api/aiem_position_sizing.py",
    "artifacts/stock-scanner-api/aiem_options_*.py",
    "artifacts/stock-scanner-api/aiem_options_pipeline.py",
    "artifacts/stock-scanner-api/aiem_options_scheduler.py",
    "artifacts/stock-scanner-api/aiem_options_dpl.py",
    "artifacts/stock-scanner-api/aiem_strat_engine/scoring.py",
    "artifacts/stock-scanner-api/aiem_strat_scheduler.py",
    "artifacts/stock-scanner-api/aiem_paper_*.py",
    # Gate self-protection — changes to the gate itself and its ledger require a TLA
    "tools/check_protected_push.py",
    "tools/trading_logic_approvals.jsonl",
]

# [TLA-<8 hex chars>] anywhere in the commit message
_TLA_RE = re.compile(r"\[TLA-([0-9a-f]{8})\]", re.IGNORECASE)

_ZEROS = "0" * 40  # git uses this for "no remote ref yet" (new branch)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str]) -> tuple[int, str, str]:
    r = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _is_protected(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in PROTECTED_PATTERNS)


def _load_approvals() -> list[dict]:
    # Read the committed blob at HEAD, not the working-tree file.
    # This ensures every approval record must already be in git history
    # before it can pass the gate — closing the working-tree-only path
    # that allowed approvals to exist on disk without ever being committed.
    result = subprocess.run(
        ["git", "show", "HEAD:tools/trading_logic_approvals.jsonl"],
        capture_output=True, text=True, cwd=REPO_DIR
    )
    if result.returncode != 0:
        return []
    records: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


# ── Core gate ─────────────────────────────────────────────────────────────────

def check_range(remote_sha: str, local_sha: str) -> tuple[bool, str, str]:
    """
    Check every commit in (remote_sha, local_sha] for TLA compliance.

    For new branches (remote_sha = 40 zeros), checks commits on local_sha
    that aren't reachable from any remote ref.

    Returns (ok, blocked_sha_12, reason).
    """
    if remote_sha == _ZEROS:
        # New branch: commits not reachable from any remote
        rc, out, err = _run(
            ["git", "log", "--format=%H", local_sha, "--not", "--remotes"]
        )
    else:
        rc, out, err = _run(
            ["git", "log", "--format=%H", f"{remote_sha}..{local_sha}"]
        )

    if rc != 0:
        return False, local_sha[:12], f"git log failed: {err}"

    commit_shas = [ln.strip() for ln in reversed(out.splitlines()) if ln.strip()]
    if not commit_shas:
        return True, "", ""   # nothing new to check

    approvals = _load_approvals()

    for sha in commit_shas:
        rc_d, diff_out, diff_err = _run(
            ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha]
        )
        if rc_d != 0:
            return False, sha[:12], f"git diff-tree failed: {diff_err}"

        changed   = [p.strip() for p in diff_out.splitlines() if p.strip()]
        protected = [p for p in changed if _is_protected(p)]

        if not protected:
            print(f"[pre-push-gate] {sha[:12]}: no protected files — PASS")
            continue

        rc_m, msg, _ = _run(["git", "log", "-1", "--format=%B", sha])
        if rc_m != 0:
            return False, sha[:12], "could not read commit message"

        match = _TLA_RE.search(msg)
        if not match:
            plist = ", ".join(protected)
            return (
                False,
                sha[:12],
                f"protected file(s) [{plist}] — no [TLA-<id>] token in commit message",
            )

        tla_id = match.group(1).lower()

        rec = next(
            (r for r in approvals if r.get("approval_id", "").lower() == tla_id),
            None,
        )
        if rec is None:
            return (
                False,
                sha[:12],
                f"[TLA-{tla_id}] not found in trading_logic_approvals.jsonl",
            )
        if not rec.get("used"):
            return (
                False,
                sha[:12],
                f"[TLA-{tla_id}] exists but used=False — approval not consumed at commit time",
            )

        print(
            f"[pre-push-gate] {sha[:12]}: protected={protected} "
            f"TLA={tla_id} used=True — PASS"
        )

    return True, "", ""


# ── Entry points ──────────────────────────────────────────────────────────────

def _hook_mode() -> int:
    """Read git pre-push stdin and check each pushed range."""
    blocked = False
    for line in sys.stdin:
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = (
            parts[0], parts[1], parts[2], parts[3]
        )
        # local_sha = all zeros means this is a branch DELETE — no commits to check.
        if local_sha == _ZEROS:
            print(f"[pre-push-gate] {_local_ref}: branch delete — PASS")
            continue
        ok, bad_sha, reason = check_range(remote_sha, local_sha)
        if not ok:
            print(
                f"[pre-push-gate] BLOCKED  ref={_local_ref}  "
                f"sha={bad_sha}  reason={reason}",
                file=sys.stderr,
            )
            blocked = True
    return 1 if blocked else 0


def _range_mode(range_arg: str) -> int:
    """--range remote_sha..local_sha invocation (daemon / manual)."""
    if ".." not in range_arg:
        print(
            f"ERROR: invalid --range '{range_arg}', expected 'remote..local'",
            file=sys.stderr,
        )
        return 2
    remote_sha, local_sha = range_arg.split("..", 1)
    ok, bad_sha, reason = check_range(remote_sha.strip(), local_sha.strip())
    if not ok:
        print(
            f"[pre-push-gate] BLOCKED  sha={bad_sha}  reason={reason}",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> None:
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--range":
        sys.exit(_range_mode(args[1]))
    else:
        # Hook mode: called by git with remote name + url as $1/$2,
        # push targets on stdin.
        sys.exit(_hook_mode())


if __name__ == "__main__":
    main()
