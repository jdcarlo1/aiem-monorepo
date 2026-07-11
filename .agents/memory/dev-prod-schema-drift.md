---
name: Dev/prod schema drift root cause
description: Why prod accumulates tables dev doesn't have, and the prevention protocol
---

# Dev/Prod Schema Drift — Root Cause and Fix

## The Rule
Every table in prod must also exist in dev before publishing. Run `dev_schema_bootstrap.sql` after any dev DB reset.

**Why:** This codebase uses inline `CREATE TABLE IF NOT EXISTS` inside Python functions — lazy schema creation. There is no ORM, no Alembic, no central migration runner. Only ONE tracked migration file exists (`001_uq_sector_alerts_date_ticker.sql`). Tables appear in a DB only after the code path that creates them has been executed. Dev doesn't run all production code paths (e.g., AIEM morning agent, ML prediction logger, probability engine), so prod accumulates tables dev never has.

**How to apply:** Before any deploy: `psql $DATABASE_URL -f artifacts/stock-scanner-api/migrations/dev_schema_bootstrap.sql`. The script covers all 60 tables found in the 2026-07-11 audit. Update the script whenever a new `CREATE TABLE IF NOT EXISTS` is added to prod code.

## 2026-07-11 Audit Results
- Prod had 281 tables, dev had 235 — 50 tables missing from dev
- 3 key tables with live data: `aiem_independent_picks` (97 rows), `aiem_ml_predictions` (229 rows), `aiem_health_log` (256 rows)
- Root cause confirmed: no migration for any of the 50 missing tables — all created inline
- Fix: created all 50 in dev with exact prod schemas (verified via information_schema diff → ZERO differences)
- Safeguard: `artifacts/stock-scanner-api/migrations/dev_schema_bootstrap.sql` (552 lines, idempotent)
- Backups at: `/tmp/backup_aiem_independent_picks.csv`, `/tmp/backup_aiem_ml_predictions.csv`, `/tmp/backup_aiem_health_log.csv`
