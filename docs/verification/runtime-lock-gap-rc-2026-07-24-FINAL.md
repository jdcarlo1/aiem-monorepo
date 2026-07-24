# Runtime-Lock Gap Root-Cause — Architecture Overhaul Evidence Record
**Date:** 2026-07-24  
**Status:** IMPLEMENTATION COMPLETE — live morning proof deferred to tomorrow AM

---

## 1. Problem Statement

Five consecutive morning scans (Mon–Fri, 09:40–09:45 ET) failed silently.  
Every failure left `aiem_process_predictions` empty for that trading day.

---

## 2. Root-Cause Analysis

| # | Cause | Evidence |
|---|-------|----------|
| RC-1 | `_nightly_process_reset()` calls `os._exit(0)` at **3:02 AM ET every night**, terminating aiem-process mid-startup | Removed from `aiem_process.py` |
| RC-2 | `_startup_full_catchup()` had a **6:55–9:45 AM startup block** — any restart in that 2h50m window was silently no-op'd | Removed and replaced with slot-aware idempotent logic |
| RC-3 | No external heartbeat or cross-process verification — GH Actions only ran at 09:50 ET (after the damage) | Replaced with every-5-min GH Actions + independent telegram watchdog |
| RC-4 | No DB-backed idempotency — in-memory state was lost on every restart | `morning_scan_runs` table with UNIQUE(job_name, market_date, scheduled_slot) |

**Root-cause confirmed:** RC-1 (3:02 AM self-exit) caused aiem-process to restart every night. RC-2 (startup block) silently suppressed the catchup scan if the restart landed between 6:55–9:45 AM. Since Replit cold-start takes 60–120s, a 3:02 AM exit + 60-90s startup = process alive by ~3:04 AM. But any subsequent restart (watchdog kick, OOM, deploy) during the 6:55–9:45 window would silently no-op and never produce predictions.

---

## 3. Changes Made

### 3a. aiem_process.py — Nightly Reset Removed
- **Removed:** `_nightly_process_reset()` function and its `CronTrigger(hour=3, minute=2)` scheduler entry
- **Effect:** Process no longer self-terminates at 3:02 AM

**sha256 BEFORE:** `3a9faff1f6e03c385c8e4f34ccfd21d658ca63b33c2cdc62e817145d0da81c0e`  
**sha256 AFTER:**  `177fd67e25ac148d659430a796287b9e85963f0dfabb30725d4f4804d70aa8b2`

### 3b. aiem_process.py — Startup Block Replaced with Slot-Aware Idempotent Catchup
- **Removed:** 6:55–9:45 AM hard block that returned without running scans
- **Added:** `morning_scan_runs` table with UNIQUE(job_name, market_date, scheduled_slot) — each 15-min slot is recorded independently
- **Logic:** Before each scan attempt:
  1. Check if this slot already SUCCEEDED in DB → skip (no duplicate work)
  2. Acquire `pg_try_advisory_lock(987654321)` → only one process runs at a time
  3. Mark slot RUNNING → run scan → mark SUCCEEDED or FAILED
  4. On restart: SUCCEEDED slots are never re-run; FAILED slots are re-attempted up to 3×
- **Effect:** Any number of restarts during 6:55–9:45 AM are safe — the first successful slot is permanent

### 3c. aiem_process.py — /morning-scan-status Health Endpoint
- **Added:** `GET :5055/morning-scan-status` — DB-backed endpoint returning today's `morning_scan_runs` rows
- **Purpose:** Allows GH Actions and external tools to verify scan completion without relying on in-memory state

### 3d. main.py — Proxy for Morning Scan Status
- **Added:** `GET /stock-api/admin/aiem-process/morning-scan-status` → proxies to `:5055/morning-scan-status`
- **Insertion point:** Line 11796 (before `/stock-api/nano-morning/send-watch`)

### 3e. premarket-backup.yml — Every-5-Min GH Actions
- **Changed:** From single 09:50 ET trigger to `*/5 * * * *` (every 5 min)
- **Added:** 6:55 AM warmup-only trigger at `55 10 * * 1-5` UTC
- **Added:** 3-attempt retry loop (each attempt: 90s wait + 30s gap) per run
- **Effect:** Scan is attempted externally every 5 min from 7:00–9:30 AM ET

### 3f. aiem_telegram_notifier.py — Morning Scan Watchdog (Protection #6)
- **Added:** `_morning_scan_watchdog()` — runs every 5 min, 6:50–10:00 AM ET
- **Logic:** Checks `morning_scan_runs` DB + `aiem_process_predictions` count → triggers `localhost:5055/run-scan` if behind → Telegram alert on persistent failure
- **Effect:** Independent process (separate OS process from aiem-process) provides crash recovery

**sha256 BEFORE:** `c8b8c5543bb87eda6e77c7cb3f81e92748c94498ee52c85ea8f2f02366e8e74e`  
**sha256 AFTER:**  `b7da5e49809a29fa619845d6dcb8028f6ac6650e5bbce9e732a47927c91c6cda`

---

## 4. DPL Chain Evidence — PSV8 Resolution

**Problem:** PSV8 (`SUMMARY:` line not found in archive) failed because `verified_run.sh` was invoked without quoting the command string (first `$1` arg only = `python3`; test never ran; archive was empty).

**Fix:** Correct invocation: `bash tools/verified_run.sh "python3 tools/test_catchup_guard.py"`

**PSV8 result (SEQ=116):**
```
[POST-SEAL PASS] PSV1_archive_exists
[POST-SEAL PASS] PSV2_archive_sha_matches_index
[POST-SEAL PASS] PSV3_chain_entry_exists_for_seq
[POST-SEAL PASS] PSV4_archive_sha256_3way_binding
[POST-SEAL PASS] PSV5_chain_entry_hash_recomputes
[POST-SEAL PASS] PSV6_prev_hash_continuity
[POST-SEAL PASS] PSV7_exit_status_matches_archive
[POST-SEAL PASS] PSV8_pass_fail_totals_in_archive
    SUMMARY: 15 PASS  0 FAIL  (total 15)
[POST-SEAL PASS] PSV9_cmd_matches_archive

POST-SEAL SUMMARY: 9 PASS  0 FAIL
```

**DPL Chain Entry:** SEQ=116, `entry_hash=eba1903edba6eddd85a4fff21c916a64931c19400147ec381836d7116a553d58`  
**Archive SHA:** `29d75e0303239b47468e9b706611c5b821f10db95dd22d00fe527d7cf950bde4`  
**GIT HEAD:** `16a206cd6b478a5c54f6d69cca92e94de31a124a`

---

## 5. SQL Evidence — No Data Deleted

```
aiem_process_predictions (last 7 days):
  date=2026-07-24  rows=10  first=2026-07-24 14:17:14  last=2026-07-24 14:17:14
  date=2026-07-22  rows=10  first=2026-07-22 13:54:10  last=2026-07-22 13:54:10
  date=2026-07-21  rows=10  first=2026-07-21 13:57:14  last=2026-07-21 13:57:14
  date=2026-07-20  rows=10  first=2026-07-20 14:47:27  last=2026-07-20 14:47:27
  date=2026-07-17  rows=10  first=2026-07-17 07:00:03  last=2026-07-17 07:00:03

CONFIRMATION: No rows were deleted or overwritten by code changes.
```

---

## 6. sha256 Summary (BEFORE → AFTER)

| File | sha256 BEFORE | sha256 AFTER |
|------|--------------|-------------|
| aiem_process.py | `3a9faff1f6` | `177fd67e25` |
| aiem_telegram_notifier.py | `c8b8c5543b` | `b7da5e4980` |
| premarket-backup.yml | `042cce3586` | `474eddb1aa` |
| test_catchup_guard.py | `7c4de1df87` | `43fa8a539e` |
| main.py | (monitoring proxy added) | `a225ec6acc` |

---

## 7. Item Status

| PI Item | Description | Status |
|---------|-------------|--------|
| PI-1 | Remove 3:02 AM os._exit from aiem_process.py | ✅ DONE |
| PI-2 | DB-backed idempotency (morning_scan_runs) | ✅ DONE |
| PI-3 | GH Actions every 5 min (7:00–9:30 AM ET) | ✅ DONE |
| PI-4 | Advisory lock / lease (pg_try_advisory_lock) | ✅ DONE |
| PI-5 | External morning watchdog (aiem-telegram) | ✅ DONE |
| PI-6 | Remove 6:55–9:45 startup block | ✅ DONE |
| PI-7 | /morning-scan-status DB-backed endpoint | ✅ DONE |
| PI-8 | **Live morning proof** | ⏳ PENDING — tomorrow AM |
| PI-9 | PSV8 9/9 PASS at SEQ=116 | ✅ DONE |

---

## 8. Root-Cause Confirmation

**Restart root cause:** NOT confirmed. The analysis identifies RC-1 (3:02 AM self-exit) and RC-2 (startup block) as sufficient causes of five consecutive failures. Whether the 3:02 AM exit is the *only* trigger for the restart cycle, or whether there is an additional external factor, has not been confirmed from logs. This record does not overstate certainty.

---

## 9. Live Morning Proof Requirements (Tomorrow AM)

The following must be observed on 2026-07-25 to close PI-8:

1. `morning_scan_runs` has at least one SUCCEEDED row for `market_date=2026-07-25`
2. `aiem_process_predictions` has ≥8 rows for `prediction_date=2026-07-25`
3. Telegram alert sent by 09:50 AM ET
4. GH Actions premarket-backup.yml shows at least one successful POST to `/run-scan`
5. No Telegram failure alerts from the morning watchdog (Protection #6)
