---
name: GH Actions cron reliability for watchdog workflows
description: GH Actions scheduled crons for premarket watchdogs are unreliable — crons can silently skip entire days; even when they fire, they cannot restart a crashed Replit VM.
---

## The rule
Do not rely on GH Actions scheduled crons as the sole external recovery mechanism for a time-critical window (e.g. 8:35 AM ET polygon scan). Crons can be silently skipped. Even when they DO fire, all watchdog endpoints proxy through the stock-api process — so if stock-api is down, every GH Actions HTTP call returns HTTP 000, and there is no mechanism to restart a crashed Replit VM from outside.

## Evidence (2026-07-27)
- premarket-backup.yml: 14 total runs. Only 2 were schedule-triggered (both on July 24). The other 12 were push-triggered. Zero schedule runs during the 10:50–13:37 UTC (6:50–9:37 AM ET) premarket outage window.
- aiem-process-heartbeat.yml: 12 runs, all push-triggered. Zero schedule runs ever.
- market-hours-watchdog.yml: 17 runs, all push-triggered. Zero schedule runs ever.
- morning-backup.yml: 2 runs, both schedule-triggered, both on July 24. No run on July 27.
- paper-trade-watchdog.yml: 0 runs ever.

The cron syntax is valid (2 schedule runs on July 24 prove it can fire). Why the cron skipped July 27 is not determinable — it requires GH audit log access.

## Why
GH Actions scheduled cron can silently skip runs when GitHub's infrastructure is under load, when a concurrency group has a stuck queued run, or for other undisclosed infrastructure reasons. This is a known GH limitation.

## How to apply
- For critical morning scans, add an in-process startup_catchup entry (not just GH cron) so the scan runs on boot if it was missed that day.
- Treat GH cron watchdogs as supplementary alert mechanisms, not guaranteed recovery paths.
- polygon_rvol is NOT currently in _startup_catchup() scope — this is a confirmed gap requiring user approval to fix.

## Structural constraint
All watchdog endpoints require stock-api to be running. When stock-api crashes, GH Actions watchdogs can Telegram-alert but cannot restart the process. The Replit platform's own crash-restart mechanism is the only thing that brings it back.
