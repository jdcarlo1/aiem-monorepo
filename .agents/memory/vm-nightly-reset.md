---
name: Daily VM OOM crash + nightly reset fix
description: Root cause of daily production crash (nclexai.org + StockScanner going down every night) and the two-layer fix applied July 2026.
---

# Daily VM OOM Crash — Root Cause & Fix

## Root Cause
The Reserved VM runs 6 processes: stock-api, aiem-process, aiem-telegram, api-server, stat-research, probability-engine. Each accumulates memory leaks. The old liveness watchdog only measured `VmRSS` of the stock-api process itself — it never saw the other 5 processes. By midnight all processes combined hit ~2 GB (VM limit) and the OS OOM-killed everything, taking down both nclexai.org and StockScanner.

**Why:** `_lw_read_proc_status()` read `/proc/self/status` (this process only). Other processes' memory was invisible.

## Fix Layer 1 — Total VM Pressure Watchdog
`_lw_read_proc_status()` now reads `MemAvailable` from `/proc/meminfo` in addition to `VmRSS`. Returns a 4th value `vm_pressure_pct = (MemTotal - MemAvailable) / MemTotal * 100` — total RAM used by ALL processes combined. Threshold: `_LW_MAX_VM_PRESSURE_PCT = 82.0`. Log now shows `vm_pressure=XX.X%`.

**How to apply:** If the watchdog ever needs to be revisited, check both `rss_pct` (this process) AND `vm_pressure_pct` (whole VM).

## Fix Layer 2 — Nightly 3 AM Memory Reset
Each service schedules a clean `os._exit(0)` at 3 AM ET (market closed, no scans):
- stock-api: 3:00 AM via APScheduler `CronTrigger(hour=3, minute=0, timezone=_ET)`, job id `nightly_memory_reset`
- aiem-process: 3:02 AM, job id `aiem_process_nightly_reset`
- aiem-telegram notifier: 3:04 AM, job id `nightly_notifier_reset`

Staggered by 2 min each so DB isn't hammered by simultaneous cold starts.

**Why `os._exit(0)` not `sys.exit()`:** `sys.exit()` raises SystemExit which APScheduler catches and swallows. `os._exit(0)` bypasses all Python exception handling and truly terminates the process so the platform restarts it.

**Result:** Memory resets from ~1.5 GB back to ~370 MB every night. Emergency watchdog catches any unexpected daytime spike.
