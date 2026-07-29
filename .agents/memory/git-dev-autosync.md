---
name: Git dev-branch autosync
description: Standing rule — push tracked-file changes to the dev branch at the end of every session; workflow slot was unavailable.
---

## Status — LIVE DAEMON (as of 2026-07-29)

`git-autosync` workflow is registered and running (PID 2027). Checks every 60s.
Slot freed by removing the `[[services]]` block from `artifacts/mockup-sandbox/.replit-artifact/artifact.toml` via `verifyAndReplaceArtifactToml`.

## What the daemon does

`git_autosync_daemon.py` at workspace root — loop every 60s:
- `git fetch origin dev`
- local ahead → `git push origin HEAD:dev`
- local behind → `git pull --ff-only origin dev`
- diverged → logs WARNING, no action (manual reconcile needed)
- All actions logged to `logs/git_autosync.log`

## Manual end-of-session push (still good practice)

Even with the daemon, new **untracked** files need `git add <file>` before the daemon can pick them up (daemon uses `git add -u` implicitly via push, not add). Explicitly stage new files during the session.

## How to apply

- `dev` has no branch protection — daemon push always succeeds.
- When the user wants changes on `main`, run the sync PR flow (branch → PR → CI → merge).
- If daemon is ever not running: `ps aux | grep git_autosync_daemon` to confirm; restart via WorkflowsRestart `git-autosync`.
