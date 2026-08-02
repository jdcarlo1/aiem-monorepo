---
name: Dev/prod separate databases — CORRECTED 2026-08-01
description: Dev and prod use SEPARATE physical databases. Earlier entry was wrong. Dev=helium/heliumdb (Replit Postgres), Prod=Neon external PG.
---

## Rule
Dev workspace and GCE deployment connect to **different** physical databases.

- Dev: `pg_host="helium"`, `pg_database="heliumdb"` (Replit-managed Postgres)
- Prod: `pg_host="ep-spring-flower-aqxm8amx.c-8.us-east-1.aws.neon.tech"`, `pg_database="neondb"` (Neon external Postgres)

**Why the old entry was wrong:**
The earlier "same DB" conclusion relied on seeing GCE git_shas in `process_lifecycle_log` from the dev workspace. This was explained by the GCE process writing to the same DATABASE_URL at a time when both processes shared a single Replit DB. Later, Neon was introduced as the production DB while dev retained the Replit Postgres. The definitive proof was the preflight endpoint response 2026-08-01: `pg_host` differed between dev and prod.

## Critical implications

- `psql "$DATABASE_URL"` in a dev session queries **dev helium only** — NOT production-valid
- Tables created in dev migrations may not exist on prod and vice versa (confirmed: `oe_daily_pipeline_jobs` absent from dev helium, present on prod Neon)
- Job heartbeat data in dev reflects dev jobs; prod heartbeats are on a separate DB
- The ADMIN_TOKEN env var differs between dev and prod — dev workspace `$ADMIN_TOKEN` will 401 on prod admin endpoints

## How to access prod data
- Use `GET /stock-api/admin/preflight` with `X-Diag-Token` — confirmed works cross-env (same DIAG_TOKEN)
- Use `GET /stock-api/admin/pipeline-checkpoint` with prod ADMIN_TOKEN (different from dev token)
- Direct psql to Neon: only possible with production DATABASE_URL (not available in dev workspace)
- Cannot use `executeSql` callback for prod (that callback connects to Replit's managed PG, which is dev)

## How to apply
- Never assume a dev DB query reflects prod state
- When diagnosing prod-only failures (e.g. oe_daily_pipeline_jobs history), use prod HTTP admin endpoints with prod auth, not raw SQL from dev
- The preflight endpoint (`X-Diag-Token`) is the universal prod-DB-identity check
