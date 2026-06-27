---
name: Comprehensive startup preload
description: All key tab caches restored from DB immediately on boot — prevents blank tabs after any server restart.
---

# Comprehensive Startup Preload

## Rule
Every key tab must have its cache restored from DB within 5 seconds of boot. Never rely on the first live scan to populate a tab.

**Why:** Reserved VM restarts at random — Replit deploys, crashes, maintenance. Users (especially weekend subscribers) see blank tabs because in-memory caches are empty post-restart and live scans take 30–120 seconds to run (or fail entirely when Yahoo is throttled).

## How to apply
The preload lives in `_startup_preload()` (around line 3677 in main.py). It runs in a daemon thread 5 seconds after boot. Add any new tab's cache here when you wire up a new endpoint.

### Sources used
| Cache attr | Source |
|---|---|
| `_unusual_calls_cache` | `unusual_calls_log` direct query (today → fallback stale) |
| `_eod_sweeps_cache` | `unusual_calls_log` with EXTRACT(HOUR) BETWEEN 18 AND 23 |
| `_cs_stk_cache` | `conviction_stack_watchlist` direct query |
| `_ms_cache`, `_mr_cache`, `_sq_cache`, `_eod_accum_cache`, `_nfmd_cache`, `_nfmc_cache`, `_sq_rad_cache`, `_conv_calls_cache`, `_comp_cache`, `_insider_trades_cache` | `_load_scan_cache(key, days_back=7)` — uses direct psycopg2, bypasses shared pool |

### Key constraints
- All connections are DIRECT psycopg2 (not the shared pool) — so the preload cannot be blocked by pool exhaustion during backfill or warm-up bursts.
- Backfill delay is **300 seconds** (5 minutes) — NOT 30 seconds — so it never races the preload or starves the pool during boot.
- Each cache is only set if `getattr(app, attr, None)` is falsy — so a live scan that finishes before preload won't be overwritten.
- All preloaded caches include `"stale": True` and `"source": "boot_preload"` so the frontend can show a "data from last scan" notice.

### Wrong table name fixed
`conviction_snapshot` does NOT exist. The correct table is `conviction_stack_watchlist`.
