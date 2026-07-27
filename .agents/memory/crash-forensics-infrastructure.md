---
name: crash-forensics-infrastructure
description: Four-gap crash forensics system — what was built, what each piece tells you after a future crash, and where to query.
---

## What was built

Four independent forensic mechanisms to ensure the next VM crash is fully diagnosable.

### Gap 1 — Exit/crash capture (process_lifecycle_log)

**Files:** `artifacts/stock-scanner-api/crash_forensics_lifecycle.py`,
`artifacts/stock-scanner-api/aiem_process_wrapper.sh`,
`artifacts/stock-scanner-api/stock_api_wrapper.sh`,
`artifacts/stock-scanner-api/notifier_wrapper.sh`

**How it works:** All three processes (aiem-process, stock-api, notifier) run via shell wrapper scripts. On start the wrapper calls `crash_forensics_lifecycle.py start <name>` → INSERT into `process_lifecycle_log`. On exit it calls `crash_forensics_lifecycle.py exit <name> $?`. Exit code 137 = SIGKILL = OOM; 0 = clean; 1 = unhandled exception; 143 = SIGTERM.

**SIGTERM trap (critical):** All three wrappers run the Python child as a background process (`... &`), then trap SIGTERM/INT. On signal: forward to child → `wait` → capture exit code → record exit → `exit $?`. Without the trap, Replit's workflow restart SIGTERM kills bash before the exit-recording code runs.

**Local fallback:** `/tmp/<process>_last_exit.json` written before the DB attempt — survives DB failure.

**Query after crash:**
```sql
SELECT process_name, started_at, exited_at, exit_code, exit_reason
FROM process_lifecycle_log ORDER BY id DESC LIMIT 10;
```

### Gap 2+4 — OOM visibility + ongoing resource monitoring (vm_resource_log)

**File:** `aiem_telegram_notifier.py` — `_vm_resource_monitor()` thread

**How it works:** The notifier reads `/proc/{pid}/status` and `/proc/{pid}/stat` every 60s for aiem-process, stock-api, and itself. Writes `vm_resource_log` (ts, process_name, pid, rss_mb, vm_pressure_pct, cpu_pct, thread_count). Retention: 7 days rolling.

**CPU%:** (tick_delta) / (elapsed_seconds × 100 Hz) × 100. First reading has no cpu_pct (no prev baseline).

**Why from the notifier:** Rows survive a complete crash of the monitored process.

**Query after crash:**
```sql
SELECT ts, process_name, rss_mb, vm_pressure_pct, cpu_pct
FROM vm_resource_log
WHERE ts > NOW() - INTERVAL '8 hours'
ORDER BY ts;
```

### Gap 3 — Persistent logging (crash_log_buffer_aiem, crash_log_buffer_notifier)

**Files:** `artifacts/stock-scanner-api/aiem_process.py` — `_AiemCrashLogTee` + flush thread → `crash_log_buffer_aiem`
`aiem_telegram_notifier.py` — `_NotifierCrashLogTee` + flush thread → `crash_log_buffer_notifier`

**How it works:** Each process wraps sys.stdout/stderr at module import time with a tee that mirrors every completed line to a 200-entry deque. A daemon thread (started from the main startup block) flushes to the DB table every 30s via fresh psycopg2.connect(). On each flush: DELETE + INSERT (full replace of the last 200 lines).

**Query after crash:**
```sql
-- aiem-process:
SELECT line_no, content FROM crash_log_buffer_aiem ORDER BY line_no;
-- notifier:
SELECT line_no, content FROM crash_log_buffer_notifier ORDER BY line_no;
```

## Key design constraint

All DB writes in all forensic mechanisms use **fresh `psycopg2.connect(connect_timeout=5)`** calls, never the app's connection pool. If the crash is pool-related, pool-routed writes also fail and lose the record.

## Forensic verdict matrix

| exit_code | vm_pressure near crash | interpretation |
|---|---|---|
| 137 | > 90% | Confirmed OOM kill |
| 137 | < 70% | SIGKILL from external cause (not OOM) |
| 1 | any | Unhandled Python exception — check crash_log_buffer_* |
| 0 | any | Scheduled self-exit (clean reset) |
| 143 | any | SIGTERM (Replit restart or deploy) |

## Live evidence

- **Exit row proof:** notifier pid=1084 → restart → `exit_code=143, exit_reason=SIGTERM, exited_at` populated (2026-07-27)
- **Crash buffer proof:** `crash_log_buffer_notifier` first flush within 30s of startup; 63 lines captured
- **All 3 processes** have start rows in `process_lifecycle_log`
