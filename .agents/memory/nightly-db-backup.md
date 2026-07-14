---
name: Nightly DB backup
description: Full pg_dump of the entire database at 2:58 AM ET, before the 3 AM nightly resets, with Telegram confirmation. 7-day rotation on disk.
---

## What it is
A scheduled `pg_dump` that creates a compressed backup of the entire PostgreSQL
database every night at 2:58 AM ET — two minutes before the first nightly
`os._exit(0)` reset at 3:00 AM. This ensures ALL data is preserved on disk
before any process restarts.

## Key facts
- **Scheduler job id**: `nightly_db_backup` in `aiem_telegram_notifier.py`
- **Time**: 2:58 AM ET (daily, not just weekdays)
- **Output**: `/home/runner/workspace/.local/backups/aiem_db_YYYYMMDD_HHMM.sql.gz`
- **Rotation**: keeps last 7 files, deletes older ones
- **Size**: ~339 MB compressed (confirmed via test run July 2026)
- **Tool**: `pg_dump` at `/nix/store/bgwr5i8jf8jpg75rr53rz3fqv5k8yrwp-postgresql-16.10/bin/pg_dump` (fallback to PATH)
- **Telegram**: sends ✅ success with size + file name, or 🚨 failure with error

## Why 2:58 AM
The nightly reset sequence is staggered:
- 3:00 AM: stock-api resets
- 3:02 AM: aiem-process resets
- 3:04 AM: aiem-telegram resets (the process running the backup job)

Running at 2:58 AM ensures the backup completes before ANY process exits.

## Why this matters for Alpaca autonomous trading
The AIEM process (and future Alpaca trading) runs independently of the website
(stock-api). If the website goes down, AIEM keeps running. The database is the
shared state. This backup ensures that even if the DB gets corrupted or data
gets accidentally deleted, there's a rolling 7-day recovery window.

**Why:** User explicitly requested data never be lost for upcoming autonomous
Alpaca trading. A crashed process can be restarted; lost DB data cannot be
recovered without a backup.

**How to apply:** If data loss is suspected, the most recent `.sql.gz` file in
`.local/backups/` can be restored with:
`gunzip -c aiem_db_YYYYMMDD_HHMM.sql.gz | psql $DATABASE_URL`
Do NOT restore to the live DB without stopping all write processes first.
