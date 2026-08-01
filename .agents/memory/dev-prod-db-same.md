---
name: Dev/prod shared DATABASE_URL
description: Correction — dev workspace and GCE deployment share the same physical database. Earlier "separate DB" conclusion was wrong.
---

## Rule
Dev workspace workflows and GCE deployment processes ALL resolve to the same DATABASE_URL (same physical Replit PostgreSQL database). PGHOST=helium, PGDATABASE=heliumdb.

**Why:**
`process_lifecycle_log` shows entries from both sha=f08e877 (GCE deployment) and sha=c3f57a5 (dev workflow) in a single query from the workspace. If they were separate DBs, GCE entries wouldn't appear. Confirmed by preflight endpoint: pg_host and pg_database are identical from dev.

## What was wrong
At 05:30 UTC 2026-08-01, the dev and GCE watchdog showed divergent alert sets (dev: aiem_auto_retire+vix_daily_fetch STALE; prod: aiem_morning_scan+aiem_continuous_research STALE). This was attributed to separate DBs. The correct cause: the GCE deployment was running sha=a9d9863 with different `_JOB_STALENESS_HOURS` thresholds than the dev workflow on sha=c3f57a5. Same DB, different code versions.

## Implications
- All prior `psql "$DATABASE_URL"` queries in dev sessions DID reflect actual production state
- Both processes write to the same tables simultaneously; dedup constraints prevent double-trades
- The real duplicate-fire risk: both workspace scheduler AND GCE scheduler fire the same CronTrigger jobs against the same DB

## How to apply
- Every "live proof" query against dev DATABASE_URL is production-valid
- When two running processes show different results for the same DB data, check code version (git_sha) before concluding separate DBs
- The preflight endpoint (`GET /stock-api/admin/preflight`, X-Diag-Token) shows pg_host+pg_database for definitive DB identity confirmation per process
