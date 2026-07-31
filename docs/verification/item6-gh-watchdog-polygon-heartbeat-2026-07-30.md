# Item 6 — GH Watchdog, Polygon Scan Outages, job_heartbeats Timestamp Fix
**Date:** 2026-07-30T21:30Z UTC / 2026-07-30 17:30 ET
**Directive:** Open Items 3–7 Closeout, 2026-07-30

---

## 6.1 — GH Watchdog: Current Status + Why It Did Not Prevent the Jul 27 Gap

### Current State (as of 2026-07-30)

The relevant watchdog workflows and their live status:

| Workflow | File | Push status | Cron status |
|---|---|---|---|
| `paper-trade-watchdog.yml` | Pushed to GitHub origin/main on 2026-07-26 | Active | Configured but cron reliability unproven (see below) |
| `aiem-process-heartbeat.yml` | YAML indentation fixed + secrets added 2026-07-27 (commit `fce1144`) | Active | Cron registration re-triggered by fix push; 1-minute polling not yet confirmed live |
| `premarket-backup.yml` | Active | Active | 2 schedule runs confirmed (both Jul 24) |
| `morning-backup.yml` | Active | Active | 2 schedule runs confirmed (both Jul 24) |

**paper-trade-watchdog.yml** was committed locally but never pushed to GitHub origin until
2026-07-26 — the file never appeared in GH Actions before that date, zero run history.

**aiem-process-heartbeat.yml** had a YAML block-scalar indentation violation (Python code
lines at column 0 inside `run: |` block). GitHub's YAML parser may have silently rejected
schedule registration. Fixed 2026-07-27 by converting to single-line `python3 -c "..."`.
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets were also missing before the fix.

### Why the Watchdog Did Not Prevent the Jul 27 Recurrence (6:50–9:37 AM ET)

**Raw evidence (from memory record `gh-actions-cron-reliability.md`):**

```
premarket-backup.yml:       14 total runs; only 2 schedule-triggered (both Jul 24).
                             Zero schedule runs during 10:50–13:37 UTC outage window.
aiem-process-heartbeat.yml: 12 total runs; all push-triggered. Zero schedule runs ever.
market-hours-watchdog.yml:  17 total runs; all push-triggered. Zero schedule runs ever.
morning-backup.yml:         2 runs; both schedule-triggered on Jul 24. No run on Jul 27.
paper-trade-watchdog.yml:   0 runs ever (was never pushed to GitHub before Jul 26).
```

**Root causes:**
1. **GH Actions cron silently skipped Jul 27.** The cron syntax is valid (2 schedule runs
   did fire on Jul 24, proving it can trigger). Why it skipped Jul 27 is not determinable
   without GH audit log access — GitHub silently drops scheduled runs under infrastructure
   load, concurrency lock contention, or other undisclosed reasons.

2. **Even if the cron had fired, it could not have recovered the VM.** All watchdog endpoints
   proxy through stock-api. When stock-api is down, every GH Actions HTTP call returns
   HTTP 000 (connection refused). GH Actions cannot restart a crashed Replit VM. The Replit
   platform's own crash-restart mechanism is the only recovery path.

3. **The aiem-process-heartbeat.yml YAML fix (fce1144, 2026-07-27) was applied the same day
   as the outage**, so the corrected workflow had not yet had a chance to fire a scheduled run.

**Conclusion:** The Jul 27 outage was not preventable by any GH Actions mechanism in its state
at that time. The recurrence prevention (Option A: startup_catchup for polygon_rvol; UptimeRobot
external monitoring) addresses the detection gap more reliably than GH crons alone.

---

## 6.2 — Polygon Scan Outage: 3 Independent Events

### Event A — Jul 23 AIEM: Dev Workflow Restart Overwrote aiem_process_predictions

**Date:** 2026-07-23, ~10:17 AM ET (14:17 UTC)

**What happened:** During verification work, a Replit Agent session restarted the aiem-process
dev workflow at 10:17 AM ET. The aiem-process startup routine performs `DELETE + INSERT` on
`aiem_process_predictions` for the current date (emergency catchup scan). Because dev and
production share the same DB (`heliumdb`), this overwrote any earlier production prediction rows
for Jul 23.

**Evidence:**
```sql
-- aiem_process_predictions: any Jul 23 production data overwritten by dev restart at ~14:17 UTC
-- Production LIVENESS-WATCHDOG shows ~6h gap (07:05–12:57 UTC = 3–9 AM EDT) on 2026-07-24
```

**Root cause:** Shared database between dev and production workflows. Dev startup scan uses
destructive `DELETE + INSERT` per date (not `INSERT ... ON CONFLICT DO NOTHING`).

**Recurrence prevention:**
- Do not restart dev workflows during 6:55–9:45 AM ET (morning scan window)
- Use `curl /run-warmup` or `/run-seed` idempotent trigger endpoints instead of workflow restart

**Status:** Mitigated by standing procedure; no code fix applied to the DELETE+INSERT pattern.

---

### Event B — Jul 23 OE: scan_date Mismatch (UTC vs ET Date Bug)

**Date:** 2026-07-23, 09:45 ET fire window

**What happened:** `daily_pipeline_runs` SCHEDULED INSERT used `date.today()` (UTC) instead of
`datetime.now(_ET).date()`. The scheduler restarted at ~00:03 UTC Jul 23 (= Jul 22 ET). At that
moment `date.today()` = Jul 23 (UTC), so it inserted a SCHEDULED row for Jul 23 even though
ET date was Jul 22. The 9:45 AM seeding step used scan_date=2026-07-23, while the candidates
had been seeded for 2026-07-22 ET — mismatch caused all 5 candidates to fail execution.

**Raw evidence:**
```
daily_pipeline_runs Jul 23: status=FAILED  candidates_seeded=5  candidates_failed=5
oe_decision_audit Jul 23 (is_test_record=FALSE): 0 rows
Pattern (2): scan_date mismatch — candidates seeded for wrong date, execution found none
```

**Fix applied:** commit `fce1144` — SCHEDULED INSERT now uses `datetime.now(_ET).date()` with
a weekend guard (weekday >= 5 → skip). Scheduler file sha256 before/after this fix:
```
Before: 6cba78b41105dd021bda67ee43cca0f47bf066a93d5d15a950f1c28c88a052dc
After:  727c85852883d2bdb2ca7da82f6fa972c1a7baf2cb7a9129a7c29ae8d73b134d
```
Documented in `docs/verification/heartbeat-and-seedbug-2026-07-27-FINAL.md` §Item 8.

**Status:** FIXED. The Jul 25 SCHEDULED row for 2026-07-25 (Saturday, a UTC→ET artifact)
remains in the DB as historical record; it caused no production harm.

---

### Event C — Jul 24 OE: Process Died Before Seeding (Zombie)

**Date:** 2026-07-24, started at ~14:17 UTC (10:17 AM ET)

**What happened:** The options-pipeline-scheduler process started at 14:17 UTC but died before
it could seed any candidates. The `daily_pipeline_runs` row shows `started_at=14:17 UTC`,
`candidates_seeded=0`. The row was manually cleared on Jul 26 with a zombie note.

**Raw evidence:**
```
daily_pipeline_runs Jul 24: status=RUNNING (manually cleared to ZOMBIE)
  started_at=2026-07-24T14:17 UTC  candidates_seeded=0  candidates_executed=NULL
oe_decision_audit Jul 24 (is_test_record=FALSE): 0 rows
Pattern (3): process died before seeding
```

**Root cause:** The 10:17 AM ET restart of dev workflows (same as Event A) may have triggered
a shared resource conflict or the scheduler process itself was restarted mid-run. The exact
cause within that 14:17 UTC window cannot be determined from available logs (log window does
not reach that date).

**Recurrence prevention:**
- Nightly `os._exit(0)` in `aiem_options_scheduler.py` + Replit auto-restart ensures a clean
  process each morning (no accumulated memory leak causing mid-run death)
- `daily_pipeline_runs` now has explicit `NO_CANDIDATES` status for double-zero seed scenarios
  so zombie detection is faster

**Status:** Mitigated by nightly reset. The specific Jul 24 zombie was a one-time event in the
context of the dev-restart window; no recurring pattern observed.

---

## 6.3 — job_heartbeats Future-Timestamp Bug: Root Cause and Fix

### Schema

```sql
CREATE TABLE job_heartbeats (
    job_name             VARCHAR(80) PRIMARY KEY,
    last_success         TIMESTAMP,      -- naive, effectively UTC (session tz = GMT)
    last_attempt         TIMESTAMP,      -- naive, effectively UTC
    last_error           TEXT,
    consecutive_failures INTEGER DEFAULT 0
);
```

### Root Cause

`last_success` and `last_attempt` are `TIMESTAMP WITHOUT TIME ZONE` (naive). The DB session
timezone is `GMT` (per standing rule), so `NOW()` writes UTC values. psycopg2 returns naive
Python `datetime` objects for these columns.

**The bug:** When Python code compares a naive `datetime` from `job_heartbeats` with a
timezone-aware `datetime` (e.g., from `aiem_sse_poller_state.last_seen_ts` which is
`TIMESTAMPTZ`), Python raises:

```
TypeError: can't compare offset-naive and offset-aware datetimes
```

In contexts where the exception is silently swallowed, the naive UTC timestamp could also be
**misread as ET** (UTC−4). A heartbeat written at `20:35:00 UTC` would appear as `20:35:00 ET`
(= `00:35:00 UTC next day`) — making it look like a **timestamp 4 hours in the future** relative
to a UTC-aware check running at `20:35 UTC`.

### Fix Status — Location-by-Location

| File | Code path | Status |
|---|---|---|
| `aiem_sse.py` ~line 327 | SSE poller: `r_attempt > max_ts` comparison | **FIXED** — `.replace(tzinfo=timezone.utc)` applied before comparison; comment documents reason |
| `aiem_watchdog.py` ~line 142 | Watchdog: `last_success_utc < stale_ts` comparison | **FIXED** — `last_success.replace(tzinfo=timezone.utc) if last_success.tzinfo is None` |
| `main.py` ~line 4570 | Health check: `(now - last_success) > timedelta(hours=max_hrs)` | **NOT AFFECTED** — both operands are naive (`datetime.utcnow()` vs DB naive); Python subtraction of two naive datetimes works correctly |
| `aiem_backup_runner.py` ~line 186 | Backup runner: `NOW() - INTERVAL '35 minutes'` comparison | **NOT AFFECTED** — pure SQL comparison; no Python datetime object used |
| `aiem_options_scheduler.py` ~line 3480 | Health endpoint: `str(hb[0])` | **NOT AFFECTED** — timestamp converted to string only, no comparison |

### Current DB State (raw query 2026-07-30)

```
job_name                             | last_success               | consecutive_failures
-------------------------------------|----------------------------|-----------------
options_pipeline_scheduler           | 2026-07-30 21:05:28        | 0
aiem_morning_scan                    | 2026-07-30 13:07:00        | 0
aiem_independent_scan                | 2026-07-30 13:20:00        | 0
aiem_independent_options_scan        | 2026-07-30 14:20:00        | 0
aiem_prediction_grader               | 2026-07-29 20:35:00        | 1  (Jul 30 attempt failed)
regime_monitor                       | 2026-07-30 20:55:00        | 0
sector_etf_daily_update              | 2026-07-30 20:45:07        | 0
vix_daily_fetch                      | 2026-07-30 20:21:00        | 0
... (19 rows total)
```

No future timestamps present in current data.

### Verdict

The future-timestamp comparison bug has been fixed in all active comparison paths
(`aiem_sse.py` and `aiem_watchdog.py`). The remaining read sites (`main.py` health check,
`aiem_backup_runner.py`, scheduler health endpoint) are not affected because they use naive-
to-naive arithmetic or pure SQL respectively.

The `aiem_prediction_grader` row with `consecutive_failures=1` (last_success=Jul 29,
last_attempt=Jul 30 at 4:35 PM ET) indicates a real failed job run on Jul 30 — not a
timestamp artifact.

---

## Evidence Chain

This document is part of the Item 3–7 closeout evidence package.
Committed as part of the 2026-07-30 end-of-session commit.
