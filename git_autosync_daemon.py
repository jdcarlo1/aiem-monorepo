#!/usr/bin/env python3
"""
git_autosync_daemon.py — Keeps origin/dev in sync with local HEAD.

Every 60 seconds:
  1. git fetch origin dev          — update remote-tracking ref
  2. Compare local HEAD vs origin/dev
  3. If local is ahead  → PRE-PUSH GATE (Python, runs before git push):
                          inspect every commit between origin/dev and HEAD;
                          any commit touching a PROTECTED_PATTERNS file must
                          carry a [TLA-<id>] token whose record in
                          trading_logic_approvals.jsonl has used=True.
                          If any commit fails: push is BLOCKED, Telegram alert
                          sent, block written to git_autosync_blocks.jsonl.
                          If all pass: git push origin HEAD:dev proceeds.
  4. If local is behind → git pull --ff-only origin dev
  5. If diverged        → log warning, do nothing (requires manual reconcile)
  6. If equal           → no-op

`git commit --no-verify` on the committing side cannot bypass this gate:
the gate runs entirely in this daemon's Python push logic, not as a git hook.
git push is never called unless _check_commits_before_push() returns ok=True.

All checks logged to logs/git_autosync.log with timestamp + hashes + action.
Blocked pushes are also appended to logs/git_autosync_blocks.jsonl (structured).
Each cycle is wrapped in try/except so one failure never kills the loop.
"""

import fnmatch
import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_DIR       = "/home/runner/workspace"
LOG_DIR        = os.path.join(REPO_DIR, "logs")
LOG_FILE       = os.path.join(LOG_DIR,  "git_autosync.log")
BLOCK_LOG      = os.path.join(LOG_DIR,  "git_autosync_blocks.jsonl")
APPROVALS_FILE = os.path.join(REPO_DIR, "tools", "trading_logic_approvals.jsonl")
INTERVAL       = 60  # seconds between each cycle

# ── Protected patterns (MUST stay in sync with tools/trading_logic_gate.sh) ──
# Any repo-relative path matching any pattern triggers the TLA requirement.
# Uses fnmatch glob syntax (same semantics as bash == glob in the shell gate).
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
]

# [TLA-<8 hex chars>] anywhere in the commit message
_TLA_RE = re.compile(r"\[TLA-([0-9a-f]{8})\]", re.IGNORECASE)

# ── Telegram ─────────────────────────────────────────────────────────────────
_TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8609255707")

# ── Logging ──────────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)sZ %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),          # also visible in workflow console
    ],
)
log = logging.getLogger("git-autosync")


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str = REPO_DIR) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _rev(ref: str) -> str | None:
    """Resolve a git ref to a full 40-char hash, or None on failure."""
    rc, out, _ = _run(["git", "rev-parse", ref])
    return out if rc == 0 and len(out) == 40 else None


# ── Pre-push gate internals ───────────────────────────────────────────────────

def _is_protected(path: str) -> bool:
    """Return True if path matches any PROTECTED_PATTERNS entry."""
    return any(fnmatch.fnmatch(path, pat) for pat in PROTECTED_PATTERNS)


def _load_approvals() -> list[dict]:
    """Load all records from trading_logic_approvals.jsonl. Returns [] on missing file."""
    if not os.path.exists(APPROVALS_FILE):
        return []
    records: list[dict] = []
    with open(APPROVALS_FILE) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _tg_send_block(sha12: str, reason: str) -> int | None:
    """
    Send a Telegram alert for a blocked push.
    Returns the Telegram message_id on success, None on failure.
    """
    if not _TG_TOKEN:
        log.warning("[pre-push-gate] TELEGRAM_BOT_TOKEN not set — alert suppressed")
        return None
    text = (
        f"\U0001f6ab [autosync] PRE-PUSH GATE BLOCKED\n"
        f"SHA: {sha12}\n"
        f"Reason: {reason}\n"
        f"Action: commit NOT pushed to origin/dev"
    )
    payload = json.dumps({"chat_id": _TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            msg_id: int | None = body.get("result", {}).get("message_id")
            log.info("[pre-push-gate] Telegram alert sent: message_id=%s", msg_id)
            return msg_id
    except Exception as exc:
        log.warning("[pre-push-gate] Telegram alert failed: %s", exc)
        return None


def _log_block(sha: str, reason: str, msg_id: int | None) -> None:
    """
    Append a structured block record to BLOCK_LOG.
    Append-only — never overwrites.  Reuses the persisted-log pattern
    used elsewhere in the stack for Telegram fix audit trails.
    """
    record = {
        "ts":               datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha":              sha,
        "reason":           reason,
        "telegram_msg_id":  msg_id,
    }
    with open(BLOCK_LOG, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def _check_commits_before_push(
    remote_hash: str,
    local_hash: str,
) -> tuple[bool, str, str]:
    """
    Delegates to tools/check_protected_push.py — single source of truth.

    Previously contained inline logic; extracted so the pre-push hook and
    daemon use the identical check and cannot silently drift apart.

    Returns (ok, blocked_sha_12, reason).  git push is ONLY called when ok=True.
    """
    import sys as _sys
    script = os.path.join(REPO_DIR, "tools", "check_protected_push.py")
    result = subprocess.run(
        [_sys.executable, script, "--range", f"{remote_hash}..{local_hash}"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    # Forward stdout (pass lines) to daemon log
    for line in result.stdout.splitlines():
        log.info("[pre-push-gate] %s", line)

    if result.returncode == 0:
        return True, "", ""

    # Parse "BLOCKED  sha=<sha12>  reason=<...>" from stderr
    import re as _re
    for line in result.stderr.splitlines():
        m = _re.search(r"sha=(\S+)\s+reason=(.+)", line)
        if m:
            return False, m.group(1), m.group(2).strip()

    # Fallback: return raw stderr as reason
    return False, local_hash[:12], result.stderr.strip() or "check_protected_push.py returned non-zero"


# ── Main sync cycle ───────────────────────────────────────────────────────────

_PAUSE_FLAG = "/tmp/autosync_paused"
# Secondary pause flag in the workspace root — survives /tmp/ clearing on
# container restart.  Both locations are honoured.
_PAUSE_FLAG_PERSISTENT = os.path.join(REPO_DIR, ".autosync_paused")


def sync_cycle() -> None:
    """One sync attempt. Raises on unexpected errors (caught by caller)."""

    # Pause guard — create /tmp/autosync_paused OR .autosync_paused (repo root)
    # to hold the daemon without stopping the workflow.  The repo-root flag
    # survives /tmp/ clearing on container restart.
    # Used during manual TLA-gated commit sequences.
    for _flag in (_PAUSE_FLAG, _PAUSE_FLAG_PERSISTENT):
        if os.path.exists(_flag):
            log.info("git-autosync PAUSED (flag=%s) — skipping cycle", _flag)
            return

    # 1. Fetch remote tracking ref
    rc, _, err = _run(["git", "fetch", "origin", "dev"])
    if rc != 0:
        log.error("fetch failed (rc=%d): %s", rc, err)
        return

    local_hash  = _rev("HEAD")
    remote_hash = _rev("origin/dev")

    if local_hash is None or remote_hash is None:
        log.error("could not resolve refs: local=%s remote=%s", local_hash, remote_hash)
        return

    if local_hash == remote_hash:
        log.info("local=%s remote=%s action=none (in-sync)", local_hash[:12], remote_hash[:12])
        return

    # Determine relationship
    # Is local ahead of remote? i.e. remote is an ancestor of local?
    rc_ahead,  _, _ = _run(["git", "merge-base", "--is-ancestor", remote_hash, local_hash])
    # Is local behind remote? i.e. local is an ancestor of remote?
    rc_behind, _, _ = _run(["git", "merge-base", "--is-ancestor", local_hash, remote_hash])

    if rc_ahead == 0 and rc_behind != 0:
        # local is strictly ahead — run pre-push gate BEFORE touching git push
        ok, bad_sha, reason = _check_commits_before_push(remote_hash, local_hash)

        if not ok:
            log.error(
                "[pre-push-gate] BLOCKED local=%s remote=%s bad_sha=%s reason=%s",
                local_hash[:12], remote_hash[:12], bad_sha, reason,
            )
            msg_id = _tg_send_block(bad_sha, reason)
            _log_block(bad_sha, reason, msg_id)
            log.error(
                "local=%s remote=%s action=PUSH_BLOCKED bad_sha=%s",
                local_hash[:12], remote_hash[:12], bad_sha,
            )
            return   # ← git push is NEVER called when gate fails

        # Gate passed — proceed with push
        rc, out, err = _run(["git", "push", "origin", "HEAD:dev"])
        if rc == 0:
            log.info("local=%s remote=%s action=PUSHED", local_hash[:12], remote_hash[:12])
        else:
            log.error("push failed (rc=%d): %s", rc, err)

    elif rc_behind == 0 and rc_ahead != 0:
        # local is strictly behind
        rc, out, err = _run(["git", "pull", "--ff-only", "origin", "dev"])
        if rc == 0:
            new_hash = _rev("HEAD") or "?"
            log.info(
                "local=%s remote=%s action=PULLED new_local=%s",
                local_hash[:12], remote_hash[:12], new_hash[:12],
            )
        else:
            log.error("pull failed (rc=%d): %s", rc, err)

    else:
        # Diverged — do nothing, require manual reconcile
        log.warning(
            "local=%s remote=%s action=DIVERGED (manual reconcile required)",
            local_hash[:12], remote_hash[:12],
        )


def main() -> None:
    log.info(
        "git-autosync daemon started — repo=%s interval=%ds log=%s "
        "pre-push-gate=ACTIVE protected_patterns=%d approvals_file=%s",
        REPO_DIR, INTERVAL, LOG_FILE, len(PROTECTED_PATTERNS), APPROVALS_FILE,
    )
    while True:
        try:
            sync_cycle()
        except Exception as exc:
            log.error("unhandled exception in sync_cycle: %s", exc, exc_info=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
