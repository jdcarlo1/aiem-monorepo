---
name: crash-forensics-infrastructure
description: Four-gap crash forensics system — what was built, what each piece tells you after a future crash, and where to query.
---

## What was built

Four independent forensic mechanisms to ensure the next VM crash is fully diagnosable.

### Gap 1 — Exit/crash capture (process_lifecycle_log)

**Files:** `artifacts/stock-scanner-api/crash_forensics_lifecycle.py`,
`artifacts/stock-scanner-api/aiem_process_wrapper.sh`,
`artifacts/stock-scanner-api/stock_api_wrapper.sh`

**How it works:** Both `aiem-process` and `stock-api` now run via shell wrapper scripts instead of directly. On start the wrapper calls `crash_forensics_lifecycle.py start <name>` which INSERTs a row into `process_lifecycle_log`. On exit it calls `crash_forensics_lifecycle.py exit <name> $?`. Exit code 137 = SIGKILL = OOM; 0 = clean; 1 = unhandled exception; 143 = SIGTERM.

**Why shell wrapper (not atexit):** SIGKILL cannot be caught in-process. Only the parent shell observes `$?` after a SIGKILL child. This is the ONLY reliable post-hoc OOM signal when dmesg is inaccessible.

**Local fallback:** `/tmp/<process>_last_exit.json` written before the DB attempt — survives DB failure.

**Query after crash:**
```sql
SELECT process_name, started_at, exited_at, exit_code, exit_reason
FROM process_lifecycle_log ORDER BY id DESC LIMIT 5;
```

### Gap 2+4 — OOM visibility + ongoing resource monitoring (vm_resource_log)

**File:** `aiem_telegram_notifier.py` — `_vm_resource_monitor()` thread

**How it works:** The notifier (most independent process) reads `/proc/{pid}/status` and `/proc/{pid}/stat` every 60s for aiem-process, stock-api, and itself. Writes `vm_resource_log` (ts, process_name, pid, rss_mb, vm_pressure_pct, cpu_pct, thread_count). Retention: 7 days rolling, deleted on each write cycle.

**CPU%:** (tick_delta) / (elapsed_seconds × 100 Hz) × 100. First reading has no cpu_pct (no prev baseline).

**Why from the notifier:** Rows survive a complete crash of the monitored process, covering the window up to and including the crash instant.

**Query after crash:**
```sql
SELECT ts, process_name, rss_mb, vm_pressure_pct, cpu_pct
FROM vm_resource_log
WHERE ts > NOW() - INTERVAL '8 hours'
ORDER BY ts;
```

### Gap 3 — Persistent logging for aiem-process (crash_log_buffer_aiem)

**File:** `artifacts/stock-scanner-api/aiem_process.py` — `_AiemCrashLogTee` class + `_start_aiem_crash_log_flush_thread()`

**How it works:** Installed at module import time (before any other code). Wraps sys.stdout and sys.stderr with a tee that mirrors every completed line to a 200-entry deque. A daemon thread (started from `main()` before `sched.start()`) flushes to `crash_log_buffer_aiem` every 30s via a fresh psycopg2.connect().

**Query after crash:**
```sql
SELECT line_no, content FROM crash_log_buffer_aiem ORDER BY line_no;
```

## Key design constraint

All DB writes in all four mechanisms use **fresh `psycopg2.connect(connect_timeout=5)`** calls, never the app's connection pool. If the crash is pool-related, pool-routed writes also fail and lose the record.

## Residual gap (noted, not fixed)

The notifier itself has no crash-log buffer or lifecycle wrapper. If the notifier crashes, the vm_resource_log trail stops and its own pre-crash logs are lost. Fixing this was explicitly out of scope per directive.

## Forensic verdict matrix

| exit_code | vm_pressure near crash | interpretation |
|---|---|---|
| 137 | > 90% | Confirmed OOM kill |
| 137 | < 70% | SIGKILL from external cause (not OOM) |
| 1 | any | Unhandled Python exception — check crash_log_buffer_aiem |
| 0 | any | Scheduled self-exit (clean reset) |
| 143 | any | SIGTERM (Replit restart or deploy) |
