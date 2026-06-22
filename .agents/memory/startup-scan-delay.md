---
name: Startup scan delay
description: _startup_scan_if_needed must have a 3-minute sleep before firing or it causes circuit breaker trip on every deploy/restart
---

## Rule
`_startup_scan_if_needed` (at the bottom of main.py) must call `time.sleep(180)` as its **first action** — before any DB check, before any yfinance call.

## Why
On boot, many warm-up jobs fire immediately: options warmer (9:45 AM cron with misfire_grace_time=600), cache warmer (15-min interval), nano/sc ranking, etc. If `_startup_scan_if_needed` also fires instantly (it runs in its own background thread), the combined burst of yfinance calls saturates Yahoo → rate-limit 429/401 → circuit breaker trips → ALL tabs show empty/stale data for the next 3 minutes (breaker cooldown). If this happens at 9:30-9:45 AM, the breaker stays tripped for the busiest window of the day.

## How to apply
- Never remove the `time.sleep(180)` from `_startup_scan_if_needed`.
- If you add new startup warm-up jobs that also hit yfinance, stagger them at least 60-90s apart.
- The 3-min delay is safe: Flask is already bound to its port (the thread is daemonized), so health checks pass during the wait.
