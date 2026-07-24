---
name: Morning VM-restart outage fix
description: Item 1/2/3 from 2026-07-24 directive — root cause, external triggers, heartbeat for missed morning scan windows
---

## Root cause (Item 1)
Agent session restarted dev workflows at 10:17 AM ET on July 23 and July 24 during verification work.
The restart triggered DELETE+INSERT in `aiem_process_predictions` which overwrote any earlier production data.
Production infra-level logs not accessible; prod LIVENESS-WATCHDOG shows a ~6h gap (07:05–12:57 UTC = 3–9 AM EDT) on 2026-07-24.
**Why:** Dev and production share the same DB. Dev workflow emergency catchup does DELETE+INSERT per date.
**How to apply:** Do not restart dev workflows during morning scan windows (6:55 AM–9:45 AM ET). Use curl to trigger instead.

## New external trigger endpoints (Item 2)
All are idempotent. Live-proven 202 on 2026-07-24.

| Endpoint | Port | Method | Notes |
|---|---|---|---|
| `/run-warmup` | 5055 (aiem-process) | POST | Calls `_SCAN_FN_REGISTRY["run_warmup"]` = `aiem_warmup()` |
| `/run-seed` | 5053 (options-scheduler) | POST | Calls `seed_daily_candidates()` in bg thread; DO NOTHING on dup |
| `/stock-api/admin/aiem-process/run-warmup` | 5050 (main.py proxy) | POST | Forwards to :5055/run-warmup |
| `/stock-api/admin/options/run-seed` | 5050 (main.py proxy) | POST | Forwards to :5053/run-seed |

## GitHub Actions coverage (Item 2)
- `premarket-backup.yml`: added `55 10 * * 1-5` cron (6:55 AM ET); window guard widened to 10:50 UTC start; 6:55 slot calls `/run-warmup` only (not full scan)
- `options-seed-trigger.yml` (NEW): fires at `40 13` and `50 13 * * 1-5` (9:40 + 9:50 AM ET); calls `/stock-api/admin/options/run-seed`

## Heartbeat system (Item 3)
- Table: `aiem_process_heartbeat` (id SERIAL, ts TIMESTAMPTZ, pid INTEGER) — CREATE IF NOT EXISTS
- Writer: thread in `aiem_process.py`, 15s initial sleep, then every 180s
- Monitor: `_aiem_morning_heartbeat_check()` in notifier, scheduled 7:05 AM ET Mon-Fri
  - Queries `WHERE ts > NOW() - INTERVAL '10 minutes'`; if no row → Telegram alert
- Live proof 2026-07-24: rows at 14:44:07Z and 14:47:08Z (3min 1sec interval), pid=3403
