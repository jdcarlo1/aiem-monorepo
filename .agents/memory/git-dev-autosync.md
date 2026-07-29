---
name: Git dev-branch autosync
description: Standing rule — push tracked-file changes to the dev branch at the end of every session; workflow slot was unavailable.
---

## Rule

At the **end of every session**, before the final reply to the user, run:

```bash
cd /home/runner/workspace
git add -u
git diff --cached --quiet || git commit -m "auto-sync: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push origin HEAD:dev
```

This keeps `dev` on GitHub current with all session changes without waiting for a PR.

## Why

All 10 Replit workflow slots are occupied by live production services (aiem-process, stock-api, notifier, options-pipeline-scheduler, probability-engine-scheduler, aiem-dashboard web, stock-scanner web, nclex-prep web, api-server, mockup-sandbox). A persistent autosync workflow cannot be registered until one slot frees.

Memory rule: `setsid`/`nohup` die on bash exit — `configureWorkflow()` is the only durable background mechanism on Replit. No slot = no persistent daemon.

## How to apply

- `git add -u` only (tracked files) — never `git add -A`. New untracked files must be explicitly staged during the session.
- `dev` has no branch protection — direct push always succeeds.
- When the user wants changes on `main`, run the sync PR flow (branch → PR → CI → merge).
- `tools/git-autosync.sh` is the canonical script; the Replit workflow registration can be completed once any production workflow slot frees.
