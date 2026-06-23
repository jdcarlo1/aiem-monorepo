---
name: Startup cache preload for unusual calls
description: Server reboot leaves tabs blank/stale until a scan completes; fix is an instant DB preload at boot.
---

## The Rule
On every server boot, a `_startup_preload` thread runs after 5 seconds and immediately loads `app._unusual_calls_cache` from the DB. This ensures the Unusual Calls tab always shows stored signals from the first page load — even before any live scan runs and even when Yahoo is rate-limited.

**Priority order:**
1. Today's signals from `unusual_calls_log` (stale=False) — served if they exist
2. Most-recent stored signals regardless of date (stale=True) — served as fallback if no today data
3. DB empty log line — only if nothing at all is in the DB

## Why this matters
Without this, after a redeploy (which happens when pushing code) the cache is empty and users see blank tabs for 5-10 minutes while the startup catch-up scan runs. With Yahoo throttled, they'd see blank tabs all day.

**How to apply:** The preload thread is in the scheduler `except` block right after `_startup_catchup` is started (~line 2669). It runs independently and does NOT block the catch-up scan — both threads run in parallel. The catch-up scan will overwrite the preload cache once it completes a fresh scan.

## Related
- `_startup_catchup` — runs 30s after boot, checks if today's data is missing, runs a scan if so
- `app._unusual_calls_cache` / `app._unusual_calls_cache_ts` — the in-memory cache used by the `/unusual-calls` endpoint
- `_load_todays_unusual_calls_from_db()` — the DB query function (defined at ~line 9004, AFTER the scheduler block, so cannot be called directly in the preload — use inline SQL instead)
