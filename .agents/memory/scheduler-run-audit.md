---
name: Scheduler run audit table
description: scheduler_run_audit tracks every 9:42 AM paper-trading run with EXECUTED/RECOVERED/SKIPPED status; wiring points in main.py
---

## Rule
Every invocation of `_aiem_paper_execute_today()` writes one row to `scheduler_run_audit`. Every restart scenario (including after-hours) writes either RECOVERED or SKIPPED — no silent misses.

## Table
```sql
scheduler_run_audit (
    id BIGSERIAL, scheduled_time TIMESTAMPTZ, actual_start_time TIMESTAMPTZ,
    status TEXT CHECK (status IN ('EXECUTED','RECOVERED','SKIPPED')),
    reason TEXT, trigger_source TEXT, exec_log_id INTEGER REFERENCES aiem_paper_execution_log(id),
    trace_id TEXT, created_at TIMESTAMPTZ
)
```
Module: `aiem_scheduler_audit.py` — `ensure_schema()`, `write_audit()`, `get_todays_audit()`.

## Two wiring points in main.py
1. After `aiem_paper_execution_log` RUNNING INSERT in `_aiem_paper_execute_today()`:
   - status=EXECUTED for trigger_source in {scheduled_942, admin_run_paper_today}
   - status=RECOVERED for trigger_source=startup_catchup
   - trace_id=`exec_{exec_id}`, exec_log_id cross-reference
2. `elif _dow < 5 and _hour_et >= 16:` in `_startup_catchup()`:
   - Checks for missing paper trades, writes SKIPPED if none found
   - actual_start_time=None, exec_log_id=None
   - Previously completely silent — this was the only gap

## Status semantics
- EXECUTED: 9:42 AM CronTrigger or admin run fired normally
- RECOVERED: server was offline at 9:42 AM; startup_catchup replayed before 4 PM ET
- SKIPPED: restart after 4 PM ET; window closed; no trades placed; explicit record

**Why:** D13 final reliability verification required proof that no scheduled run can be silently missed after a VM crash/restart. The after-hours (hour >= 16) branch was the only path with zero DB trace.

**How to apply:** Any new scheduled job added to main.py that has a startup_catchup equivalent must also write to scheduler_run_audit (or a parallel audit table) for completeness.
