---
name: Liveness watchdog cold-start grace period
description: Watchdog health-check must not count failures during prod cold start — routes not yet registered = 404 = os._exit(1) = promote fails
---

## Rule
Any liveness watchdog that calls `os._exit` on consecutive health-check failures **must** include a startup grace period before it starts counting failures.

## Why
`main.py` is 20k+ lines. The liveness watchdog thread starts at line 432, but `@app.route` decorators (Flask route registration) run from line 435 through the end of the file. On prod cold start, Python importing numpy/pandas/sklearn/xgboost/psycopg2 takes 60-120s before all routes are registered. The watchdog fires at T+30s, T+60s, T+90s — all three get 404 from an unregistered `/stock-api/` route → 3 consecutive failures → `os._exit(1)`. This killed the stock-api process inside the Replit promote window (consistently at ~2m55s after image push), causing every deploy to fail.

Last successful deploy: 2026-07-10 (before watchdog was added). Both 2026-07-13 deploys failed after watchdog was added (commits 4d57dfa / 07926a3 at 14:33-14:41Z).

## How to apply
- Set `_LW_STARTUP_GRACE_SECS = 150` (2.5 minutes).
- Record `_lw_boot_time = _lw_time.time()` at the top of the watchdog function.
- Inside the watchdog loop, **before** the health-check `try:` block, check elapsed time and `continue` (resetting `consecutive_failures = 0`) if still within grace.
- Resource checks (threads, RSS, vm_pressure) can still run during grace — they read `/proc` and never call `os._exit` during normal startup.
- The grace period applies **only** to the health-check failure counter, not to the hard resource-limit exits.
- Implemented at `main.py` lines 353, 360, 412-414.
