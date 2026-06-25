---
name: Market-open scan must fire after 9:30 AM
description: The unusual-calls "market-open" cron slot must be scheduled after 9:30 AM or _intraday_scan_allowed() will always block it
---

## The bug
The unusual-calls market-open scan was scheduled at `hour=9, minute=5` (9:05 AM ET).
`_intraday_scan_allowed()` requires `570 <= mins <= 990` = 9:30 AM–4:30 PM.
At 9:05 AM: `9*60+5 = 545 < 570` → returns False → scan prints "market closed (holiday/weekend)" and exits.

**This silently skipped the first scan of every trading day.** The tabs showed stale overnight data until the 10:05 AM slot ran.

## The fix (applied June 25, 2026)
Moved the cron from `hour=9, minute=5` → `hour=9, minute=36`.
At 9:36 AM: `9*60+36 = 576 >= 570` → passes → scan runs.
Updated the startup log message to reflect 9:36 instead of 9:05.

## Rule
Any scheduler job that calls `_intraday_scan_allowed()` internally MUST be scheduled at or after 9:30 AM ET, or it will always be blocked. Pre-market jobs (8:00–9:29 AM) must either:
- Not call `_intraday_scan_allowed()` (use the `_force` flag or a direct holiday check), OR
- Accept that they will be skipped and use a different guard

**Why:** `_intraday_scan_allowed()` enforces the 9:30 AM market-open floor to prevent live Yahoo/Tradier calls before options data is available. Pre-market jobs that use overnight/EOD data should bypass this with `_force=True` or a dedicated guard.

**How to apply:** Before adding any cron job that calls a function with an `_intraday_scan_allowed()` gate, verify the scheduled time is ≥ 9:30 AM ET, or explicitly pass `_force=True`.
