---
name: Morning scan architecture overhaul (2026-07-24)
description: Three-layer fix for five consecutive morning scan failures — root causes RC-1 (3:02 AM self-exit) + RC-2 (6:55-9:45 startup block), and the idempotent DB-backed replacement
---

## Root Causes

| # | Cause | Where |
|---|-------|-------|
| RC-1 | `_nightly_process_reset()` called `os._exit(0)` at **3:02 AM ET nightly** — process restarted every night | `aiem_process.py` |
| RC-2 | `_startup_full_catchup()` had a **6:55–9:45 AM hard block** — any restart in that 2h50m window silently no-op'd | `aiem_process.py` |

**RC-2 is the silent failure.** RC-1 triggered a restart; if anything then kicked the process again during 6:55–9:45 (OOM, watchdog, deploy), the catchup was silently suppressed. No error, no alert, empty predictions.

## Three-Layer Fix

**Layer 1 — Remove self-exit (aiem_process.py):**
- `_nightly_process_reset()` and its `CronTrigger(hour=3, minute=2)` entry removed entirely.

**Layer 2 — DB-backed idempotent catchup (aiem_process.py):**
- `_startup_full_catchup()` rewritten: creates `morning_scan_runs` table with `UNIQUE(job_name, market_date, scheduled_slot)`.
- Before each 15-min slot scan: checks if SUCCEEDED in DB → skip; acquires `pg_try_advisory_lock(987654321)` → only one process at a time.
- Marks slot RUNNING → run → SUCCEEDED or FAILED (retries up to 3×).
- Any number of restarts are safe — first SUCCEEDED slot is permanent; never deletes existing predictions.

**Layer 3 — External watchdogs:**
- GH Actions `premarket-backup.yml`: every 5 min, 7:00–9:30 AM ET (+ 6:55 AM warmup-only).
- `_morning_scan_watchdog()` in `aiem_telegram_notifier.py` (Protection #6): checks DB + predictions count every 5 min, 6:50–10:00 AM ET. Triggers `:5055/run-scan` on miss; Telegram alert on persistent failure.

## Endpoint Added
- `GET :5055/morning-scan-status` on aiem-process health server → DB-backed today's `morning_scan_runs` rows.
- `GET /stock-api/admin/aiem-process/morning-scan-status` proxy in `main.py` (line ~11796).

## Live Proof Gate
PI-8 (live morning proof) is deferred to 2026-07-25 AM. Check:
1. `morning_scan_runs` has ≥1 SUCCEEDED row for `market_date=2026-07-25`
2. `aiem_process_predictions` has ≥8 rows for `prediction_date=2026-07-25`
3. No watchdog failure alerts in Telegram

## How to apply
- If morning scans miss again: check `morning_scan_runs` table first (DB truth) before looking at in-memory state.
- The advisory lock key is **987654321** — if it shows held but no scan is running, a crash left the lock held; `pg_advisory_unlock(987654321)` releases it.
- `morning_scan_runs` table is created by the catchup function on first run. If missing: it will be auto-created; no manual schema step needed.
