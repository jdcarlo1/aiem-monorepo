---
name: aiem-process watchdog in notifier
description: Background thread inside aiem_telegram_notifier.py that auto-recovers aiem-process after nightly reset failure
---

## Problem
aiem-process does `os._exit(0)` at 3:02 AM ET nightly to flush memory. Replit occasionally fails to auto-restart the workflow, leaving it dead all day with no alert. This caused missed 9:20 AM independent stock pick scans and missed Telegram alerts (confirmed incident July 13, 2026).

## Solution
A daemon thread inside `aiem_telegram_notifier.py` (the always-on notifier) that acts as a permanent watchdog. No new workflow needed — fits within the 10-workflow limit.

## Implementation
- **Location**: `main()` in `aiem_telegram_notifier.py`, function `_aiem_process_watchdog()`
- **Starts**: immediately when notifier boots (30s initial sleep to let health server bind)
- **Check interval**: every 120 seconds via `pgrep -f aiem_process.py`
- **Grace window**: 3:00–3:10 AM ET — skips checks during the nightly reset; misses reset to 0
- **Threshold**: 2 consecutive misses (≥4 min down) before acting
- **On miss threshold**:
  1. Sends Telegram alert: "⚠️ AIEM-PROCESS IS DOWN"
  2. Spawns `aiem_process.py` as a detached subprocess (new session, stdout → `/tmp/aiem_process_watchdog_spawn.log`)
  3. Waits 15 seconds, confirms alive, sends success or failure alert
- **Alert cooldown**: 30 min — doesn't spam if repeated failures
- **Fail-open**: pgrep errors don't trigger false positives

**Why:** The notifier is already running 24/7 and has Telegram access — embedding the watchdog there avoids needing a 12th workflow (platform limit is 10). All three 3 AM resets (stock-api 3:00, aiem-process 3:02, notifier 3:04) are staggered so the notifier is always alive to watch aiem-process restart.

**How to apply:** If you ever add a new nightly reset to the notifier itself, adjust the grace window end time to cover it. The spawn log is at `/tmp/aiem_process_watchdog_spawn.log`.
