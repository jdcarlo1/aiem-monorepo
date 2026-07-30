---
name: Options scheduler does not hot-reload code
description: The options-pipeline-scheduler process does not detect or reload changed .py files at runtime; code fixes require an explicit workflow restart to take effect.
---

## Rule
After any commit that changes `aiem_options_scheduler.py`, the options-pipeline-scheduler workflow **must be explicitly restarted** before the new code takes effect. Python imports are cached in `sys.modules`; the scheduler never re-reads the file.

**Why:** Confirmed empirically: commit d0ebf62 (NO_TRADE_GATES fix) was pushed at 13:24Z. The scheduler process (started 12:57Z with b81c909 pre-fix code) continued running until explicitly restarted 90+ minutes later. All jobs during that window logged `[exec] FAILED` instead of `[exec] NO_TRADE_GATES`. Restarting the workflow (WorkflowsRestart) caused the next gate-rejected job (DUOL, 14:22Z) to correctly log `[exec] NO_TRADE_GATES`.

**How to apply:** After any aiem_options_scheduler.py change: restart `artifacts/stock-scanner: options-pipeline-scheduler`. This is distinct from stock-api which has a staleness guard that auto-restarts. The scheduler has no staleness guard.

## Corollary: daily_pipeline_runs counters are informational
`candidates_seeded` and `candidates_failed` in `daily_pipeline_runs` use ON CONFLICT DO UPDATE (last-writer-wins). When multiple callers write on the same day (e.g., /run-seed + natural 09:40 cron, or /run-now + natural 09:45 cron), the final values reflect only the last writer's count.

**Authoritative counts:** `SELECT COUNT(*) FROM options_pipeline_jobs WHERE scan_date=<date>` grouped by status. Not `daily_pipeline_runs.candidates_*`.

Code comments documenting this are at aiem_options_scheduler.py lines ~603 (seed write site) and ~2845 (worker write site).
