#!/usr/bin/env python3
"""
git_autosync_daemon.py — Keeps origin/dev in sync with local HEAD.

Every 60 seconds:
  1. git fetch origin dev          — update remote-tracking ref
  2. Compare local HEAD vs origin/dev
  3. If local is ahead  → git push origin HEAD:dev
  4. If local is behind → git pull --ff-only origin dev
  5. If diverged        → log warning, do nothing (requires manual reconcile)
  6. If equal           → no-op

All checks logged to logs/git_autosync.log with timestamp + hashes + action.
Each cycle is wrapped in try/except so one failure never kills the loop.
"""

import os
import subprocess
import time
import logging
from datetime import datetime, timezone

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_DIR  = "/home/runner/workspace"
LOG_DIR   = os.path.join(REPO_DIR, "logs")
LOG_FILE  = os.path.join(LOG_DIR,  "git_autosync.log")
INTERVAL  = 60  # seconds between each cycle

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


def _run(cmd: list[str], cwd: str = REPO_DIR) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _rev(ref: str) -> str | None:
    """Resolve a git ref to a full 40-char hash, or None on failure."""
    rc, out, _ = _run(["git", "rev-parse", ref])
    return out if rc == 0 and len(out) == 40 else None


def sync_cycle() -> None:
    """One sync attempt. Raises on unexpected errors (caught by caller)."""

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
    rc_ahead, _, _ = _run(["git", "merge-base", "--is-ancestor", remote_hash, local_hash])
    # Is local behind remote? i.e. local is an ancestor of remote?
    rc_behind, _, _ = _run(["git", "merge-base", "--is-ancestor", local_hash, remote_hash])

    if rc_ahead == 0 and rc_behind != 0:
        # local is strictly ahead
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
            log.info("local=%s remote=%s action=PULLED new_local=%s", local_hash[:12], remote_hash[:12], new_hash[:12])
        else:
            log.error("pull failed (rc=%d): %s", rc, err)

    else:
        # Diverged — do nothing, require manual reconcile
        log.warning(
            "local=%s remote=%s action=DIVERGED (manual reconcile required)",
            local_hash[:12], remote_hash[:12],
        )


def main() -> None:
    log.info("git-autosync daemon started — repo=%s interval=%ds log=%s", REPO_DIR, INTERVAL, LOG_FILE)
    while True:
        try:
            sync_cycle()
        except Exception as exc:
            log.error("unhandled exception in sync_cycle: %s", exc, exc_info=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
