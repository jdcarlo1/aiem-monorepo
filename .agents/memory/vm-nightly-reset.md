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

## Fix Layer 2 — Nightly 3 AM Memory Reset (UPDATED July 2026)

**CRITICAL: os._exit(0) in production causes crash loops.**

When any process exits in the monorepo deployment, the platform cascade-SIGTERMs all other processes, then tries to restart all 4 runnable services simultaneously. If they don't come up fast enough, healthchecks time out and the deployment enters a crash loop that can last hours (confirmed: nclexai.org down all morning July 20, 2026).

**Fix (July 2026):** All three nightly reset functions now check `os.environ.get("REPLIT_DEPLOYMENT") == "1"`:
- If **production**: call `gc.collect()` only — NO exit. Memory relief is partial but the site stays up.
- If **dev**: keep `os._exit(0)` — platform restarts individual dev workflow, no cascade.

**Why `os._exit(0)` not `sys.exit()`:** `sys.exit()` raises SystemExit which APScheduler catches and swallows. `os._exit(0)` bypasses all Python exception handling. This distinction only matters in dev now.

Schedule (unchanged):
- stock-api: 3:00 AM ET, job id `nightly_memory_reset`
- aiem-process: 3:02 AM, job id `aiem_process_nightly_reset`
- aiem-telegram notifier: 3:04 AM, job id `nightly_notifier_reset`

**Result:** Memory resets from ~1.5 GB back to ~370 MB every night in dev. In prod, gc.collect() gives partial relief; the vm_pressure watchdog (82% threshold) handles any daytime emergency.
