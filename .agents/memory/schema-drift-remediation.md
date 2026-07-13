---
name: Schema drift remediation — July 2026
description: Full dev/prod reconciliation completed; migration tracking system established; what the remaining drift items are and why they're safe.
---

## Reconciliation completed 2026-07-13

All 32 tables with schema differences were resolved. Strategy: fix dev to match prod (not the reverse). Zero prod rows touched.

**What was done:**
- Dropped `aiem_supervisor_signal_health` from dev (was created in prod by Python app at runtime, never tracked by Replit; bootstrapped empty in dev, causing "already exists" hard block)
- Added 42 prod-only columns across 13 dev tables
- Added 4 PKs to dev (aiem_finding_embeddings, aiem_ticker_reference_cache, ticker_lifecycle, vix_daily)
- Added 13 UQ constraints to dev
- Fixed type/nullable diffs in dev (supervisor_loop_audit, quant_agent_sessions, prediction_outcomes, process_predictions)
- Restructured morning_watchlist in dev: dropped id SERIAL PK → ticker VARCHAR PK, added notes column

**Root cause:** Schema changes were applied directly to prod at runtime for months (Python app's CREATE TABLE IF NOT EXISTS / ALTER TABLE on startup) and never reflected back into dev. Replit's migration system compared dev to its own stale baseline, not to live prod.

## Remaining drift after remediation (safe, not blockers)

7 items remain, all additive only:
1. `aiem_module_registry` — stage_name, registry_source (dev-only → added to prod by Replit)
2. `aiem_signal_discoveries` — signal_name (dev-only → added to prod)
3. `aiem_squeeze_signals` — 6 dev-only cols → added to prod
4. `layer9_scores` — 6 dev-only cols → added to prod
5. `quant_agent_sessions.question` — dev nullable, prod NOT NULL → Replit relaxes prod, safe
6. `aiem_paper_trades` — dev has extra UNIQUE(ticker,trade_date); prod has 0 duplicates → safe add
7. `aiem_supervisor_loop_audit` — dev has UNIQUE(audit_trace_id); prod has 0 duplicates → safe add

7 tables only in dev → safely created in prod on publish (no data migration).

## Migration tracking system (Part 2)

- `artifacts/stock-scanner-api/migrations/applied/` — timestamped SQL migration files (format YYYYMMDD_HHMMSS_description.sql)
- `artifacts/stock-scanner-api/migrations/verify_drift.sh` — pre-publish drift check script
- **Protocol:** Run verify_drift.sh at end of every build session. Report count + any pending items.
- **Important:** The `_backup_20260709` tables have no PKs deliberately — they are snapshot copies, not live tables.

## How to prevent recurrence

Every schema change to dev must be captured as a migration file at time of change, not at deploy time.
The Python app's runtime CREATE TABLE IF NOT EXISTS pattern is the long-term drift source — any new runtime-created table or column must be immediately added to a migration file.

## Evidence

- verified_run.sh / verify_chain.sh used throughout; all commands logged
- Full information_schema comparison run dev vs prod before any changes
- All changes dev-only; prod never written (read-only executeSql for prod queries)
