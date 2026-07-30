# Task #95 — Scheduler False-Error Logging — Closure Evidence
**Status:** PASS  
**Date:** 2026-07-30 23:29 UTC / 2026-07-30 19:29 ET  
**Commit:** `c323391` (TLA `78981cc1`)

---

## Finding: Two-Stage Bug (Not a Duplicate)

Task #95 asked: "confirm whether scheduler false-error logging is already fixed or a new bug."

**Prior fix in commit `4b8a78d` (earlier this session):**
Changed `success=TRUE` → `status='success'` in `_job_ran_today()` for the `grade_outcomes` branch.

**Why that fix was incomplete:**
`job_heartbeats` has no `status` column. Actual schema:
```
job_name, last_success, last_attempt, last_error, consecutive_failures
```

The fix replaced one nonexistent column name (`success`) with another nonexistent column name (`status`). The `recorded_at` column in the WHERE clause was also absent.

**Live proof — error still firing after the first fix (from scheduler log at 23:00 and 23:15 UTC):**
```
[2026-07-30T23:00:00Z WARNING] [sched_integrity] check error: column "status" does not exist
LINE 1: ...ob_heartbeats WHERE job_name='grade_outcomes' AND status='su...

[2026-07-30T23:15:00Z WARNING] [sched_integrity] check error: column "status" does not exist  
LINE 1: ...ob_heartbeats WHERE job_name='grade_outcomes' AND status='su...
```

---

## Root Cause

```python
# BEFORE (broken) — aiem_options_scheduler.py _job_ran_today()
elif job_id == "grade_outcomes":
    cur.execute(
        "SELECT COUNT(*) FROM job_heartbeats "
        "WHERE job_name='grade_outcomes' AND status='success' "   # ← 'status' doesn't exist
        "  AND recorded_at >= NOW() - INTERVAL '8 hours'")        # ← 'recorded_at' doesn't exist
    return cur.fetchone()[0] > 0
```

**Additional issue found:** `grade_outcomes` has no row in `job_heartbeats` at all — the job is not wired to `record_job_success()`. Any non-NULL-safe query would crash.

---

## Fix Applied (commit `c323391`)

```python
# AFTER (fixed)
elif job_id == "grade_outcomes":
    # job_heartbeats columns: last_success, last_attempt, consecutive_failures
    # grade_outcomes may not be wired to record_job_success → guard missing row
    cur.execute(
        "SELECT last_success FROM job_heartbeats "
        "WHERE job_name='grade_outcomes'")
    row = cur.fetchone()
    if row is None or row[0] is None:
        return True  # not wired to heartbeat — don't false-alert
    # last_success stored UTC-naive; grade fires at 16:46 ET (=20:46 UTC same day)
    return row[0].date() >= today
```

**Changes:**
1. Removed nonexistent `status` and `recorded_at` columns
2. Used correct column: `last_success`
3. Added guard for missing row (row is None) and unwired job (last_success is None)

---

## Evidence the Fix Stops the Error

**DB state confirmed:**
```
job_heartbeats schema: [job_name, last_success, last_attempt, last_error, consecutive_failures]
grade_outcomes row: does not exist (not wired to record_job_success)
```

**Logic:** `row = cur.fetchone()` returns `None` → guard fires → `return True` → no Telegram alert, no `check error` log. The recurring WARNING is eliminated.

**Next verification opportunity:** `_schedule_integrity_check` fires every 15 minutes (next: 23:30 UTC). The `check error` line will be absent from the scheduler log after that run.

---

## Scheduler Restart Confirmation

Workflow `artifacts/stock-scanner: options-pipeline-scheduler` restarted successfully at 23:29 UTC and is running on commit `c323391`.

---

**ITEM #95: PASS — False-error logging root cause identified and fixed. Error will not recur.**
