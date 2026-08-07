# Morning deploy blackout — why AIM picked 0 on 2026-08-07

## What happened

| Time (ET) | Event |
|---|---|
| ~09:05 | Agent opened **PR #46** (`main` → `dev`) to sync ~27 missing PRs onto live |
| **09:17** | PR #46 merged to `dev` |
| ~09:28 | First Publish claimed; live Python process still on old build |
| ~09:42 | Workspace pull/restart mid-window; Publish URL still stale |
| **09:57** | `nclexai.org` stock-api process start (uptime metrics) — **after** Loop B 9:07, paper 9:42, Loop B watchdog 9:45 |
| ~10:02 | Second Publish finally showed new Pattern Lab keys |

Result: **0** `aiem_paper_trades` with `trade_date=2026-08-07`.

This was **not** a mysterious overnight crash. Live was ~182 commits behind `main`; catching up required a Publish **during** the autonomous morning window.

## Why catchup alone did not save the day

1. External **Paper Trade Daily Watchdog** GH Action is `disabled_manually` (quota).
2. Only `scheduled_942` could override a zero-pick `SKIPPED` ledger row.
3. Internal watchdog treated `SKIPPED` as fully terminal — never retried `NO_CANDIDATES`.
4. Early `startup_recovery` after a late boot can mark `SKIPPED` before Loop B/scanner warm → day stuck at 0.

## Code fixes (this change)

1. **Step 2c** — zero-pick override also for `scheduled_1015`, `startup_*`, watchdogs, `admin` (cap 5 for non-cron).
2. **Internal watchdog** — retries zero-pick `COMPLETED`/`SKIPPED`.
3. **`scheduled_1015`** cron — paper retry at 10:15 ET Mon–Fri.
4. **Late-boot reconciler** — waits up to 6 min for Loop B predictions before first paper attempt; does not bail on zero-pick `SKIPPED`.
5. **Boot Telegram** — alerts if stock-api starts Mon–Fri 08:50–10:20 ET.

## Ops rule (non-negotiable)

**Do not Publish / restart stock-api Mon–Fri 08:50–10:20 ET.**

- Prefer: Publish **before 08:45 ET** or **after 10:30 ET**.
- Large catch-up syncs (`main`→`dev`): Publish the night before, never mid-morning.
- Live branch is Replit **`dev`** — merging only to `main` does not update production.
