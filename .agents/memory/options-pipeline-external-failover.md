---
name: Options pipeline external failover
description: GitHub Actions backup runner + every-minute watchdog that fire when the primary Replit VM misses the 9:40/9:45 AM options pipeline window. Covers architecture, dedup contract, and secrets required.
---

## Architecture

Three layers of external protection (all in `.github/workflows/`):

| Layer | File | Schedule | Purpose |
|---|---|---|---|
| Backup runner | `morning-backup.yml` | 9:50 AM + 10:10 AM ET | Post-window fallback if primary missed seed/execute |
| Watchdog | `market-hours-watchdog.yml` | Every minute, 9AM–4PM ET | Checks 4 checkpoints; triggers backup runner if failed |
| Backup runner script | `aiem_backup_runner.py` | Called by watchdog | All trade/dedup logic; advisory lock |

## Watchdog checkpoints (`aiem_watchdog.py`)

1. `vm_heartbeat` — `job_heartbeats.last_success` for `options_pipeline_scheduler` within 30 min
2. `polygon_scan` — `polygon_rvol_scan` rows for today > 0 (after 9:15 ET) — **alert-only**, backup runner cannot fix this
3. `seed_9_40` — `options_pipeline_jobs` rows for today > 0 (after 9:55 ET) — triggers recovery
4. `pipeline_9_45` — all jobs DONE (after 10:10 ET) — triggers recovery

Recovery window: 9:55 AM – 3:00 PM ET only. Outside that window: alert-only.

## Dedup contract (3 layers)

1. `daily_pipeline_runs` table — UNIQUE(run_date, trigger_source); COMPLETED/RUNNING status → skip
2. `options_pipeline_jobs` — all DONE for today → skip  
3. `job_heartbeats` for `backup_runner_*` within last 25 min → skip

## Advisory lock

`pg_try_advisory_lock(9400945)` — session-level, prevents concurrent seed/execute from primary + backup simultaneously. Backup waits up to 2 minutes then yields if primary is mid-run.

## `daily_pipeline_runs` table

Written by primary scheduler at:
- Startup: `(run_date, 'primary', 'SCHEDULED')` — this is the dedup signal for the backup runner
- `seed_daily_candidates()` → `status='RUNNING', candidates_seeded=N`
- `run_pipeline_worker()` → `status='COMPLETED'/'FAILED', candidates_executed/no_trade/failed=N`

Written by backup runner on completion under `trigger_source='backup_github_actions'` (or whatever `TRIGGER_SOURCE` env var says).

## GitHub Actions secrets required

Add in repo Settings → Secrets → Actions:
- `DATABASE_URL` — full Postgres connection string
- `POLYGON_API_KEY`
- `TRADIER_API_TOKEN_2`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID` (value: 8609255707)

**Why:** Without these the watchdog exits cleanly (no crashes) but cannot do anything useful.

## Key design rules

- Watchdog **never** executes trades directly — it only calls `aiem_backup_runner.py` as a subprocess
- Backup runner uses rule-based scoring (not LLM) since it has no access to `aiem_options_pipeline.py`
- Advisory lock prevents race between primary startup recovery and backup run
- `polygon_rvol_scan` is populated by `aiem_process.py` 8:35 AM job — backup runner cannot backfill it; watchdog alerts only
- `polygon_market_daily` is always EOD — both primary and backup use `MAX(scan_date)` / `ORDER BY scan_date DESC LIMIT 1`

## Files

- `artifacts/stock-scanner-api/aiem_watchdog.py` — standalone Python watchdog
- `artifacts/stock-scanner-api/aiem_backup_runner.py` — standalone recovery runner
- `.github/workflows/market-hours-watchdog.yml` — every-minute GH Actions cron
- `.github/workflows/morning-backup.yml` — 9:50 AM + 10:10 AM cron
