---
name: Publish hang root cause — polygon_market_daily lock cascade
description: Why Replit's "Generating database migrations..." step hung for 9+ hours and how to diagnose it
---

# Publish Migration Hang — Root Cause

## The rule
Any `ALTER TABLE` on `polygon_market_daily` in the dev DB will queue behind the polygon scanner's continuous `AccessShareLocks`. While the ALTER is queued, all subsequent readers are also blocked (lock queue effect). This creates a cascade of 20+ blocked sessions. Replit's migration generator, when it arrives, cannot introspect the dev schema and hangs indefinitely.

**Why:** `polygon_market_daily` is read continuously by the polygon scanner workflow (8:35 AM scan + any mid-session queries). PostgreSQL's lock queue means an `AccessExclusiveLock` request blocks all subsequent `AccessShareLock` requests even before the exclusive lock is granted.

**How to apply:** Never run `ALTER TABLE polygon_market_daily` during market hours or while the stock-api workflow is running scans. Run only during off-hours (after 6 PM ET) or after stopping the stock-api workflow temporarily.

## How to diagnose a hung publish
```sql
-- Check for blocked sessions
SELECT count(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;

-- Find root blocker
SELECT pid, wait_event_type, wait_event, left(query,100)
FROM pg_stat_activity
WHERE cardinality(pg_blocking_pids(pid)) = 0
  AND pid IN (SELECT unnest(pg_blocking_pids(p.pid)) FROM pg_stat_activity p WHERE cardinality(pg_blocking_pids(p.pid)) > 0);
```

## The correct tool for dev function inventory
Use `pg_proc JOIN pg_namespace` — NOT `information_schema.routines`. The routines view is incomplete (misses many trigger functions). As of 2026-08-01, dev has 39 functions in public schema.

## Schema diff state (as of 2026-08-01 publish)
- 3 tables in dev not prod: `autosync_alert_log`, `autosync_protected_file_log`, `job_attempt_log`
- 1 column in dev not prod: `polygon_market_daily.captured_at TIMESTAMPTZ NULLABLE`
- 0 drops (additive-only diff)
- Applied to prod via publish flow 2026-08-01
