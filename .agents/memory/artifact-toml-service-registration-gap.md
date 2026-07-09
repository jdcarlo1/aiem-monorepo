---
name: Background worker missing from artifact.toml never runs in prod
description: A Python/Node background script can run fine as a dev `.replit` workflow yet never execute in production if it has no `[[services]]` block in that artifact's artifact.toml.
---

## The gap
Each artifact's deployed production services are defined ONLY by `[[services]]` blocks in `artifacts/<slug>/.replit-artifact/artifact.toml`. Top-level `.replit` `[[workflows.workflow]]` entries (including ones auto-listed under an artifact's name in the workspace UI) are a dev-only convenience — they do NOT get deployed. If a background worker (scheduler, notifier, scanner daemon) only exists as a `.replit` workflow and was never added to artifact.toml via `verifyAndReplaceArtifactToml`, it silently never starts after a deploy, with no error surfaced anywhere.

## How this was found
StockScanner AI's `aiem_process.py` (fires the daily S1B/S1C/S1D Telegram "Morning Picks" alert) ran fine in dev but had zero rows ever written to its prod DB table and zero matching startup-log lines in `fetch_deployment_logs` — proof it had literally never started in production. `artifact.toml` had `web`, `stock-api`, `aiem-telegram` services but no `aiem-process` entry. A sibling script (`daily_scheduler.py` / probability-engine-scheduler) has the identical gap and was flagged but not yet fixed (lower priority, not what the user complained about).

**Why:** artifact.toml is the single source of truth for what deploys; `.replit` workflow blocks are workspace/dev scaffolding only, and nothing warns you when they diverge.

**How to apply:** whenever a user reports a feature/alert/cron job that "used to work" or "works in the editor but not live," check whether every long-running script it depends on has a matching `[[services]]` block in that artifact's artifact.toml — not just a `.replit` workflow — before looking anywhere else.
