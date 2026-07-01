---
name: Heartbeat fire-and-forget anti-pattern
description: record_job_success can be called immediately after starting a background thread, before the thread's actual work has run or completed — a green heartbeat is not proof the job did anything.
---

In stock-scanner-api/main.py, `_run_continuous_research_job()` (the 6PM Mon-Fri hypothesis-testing scheduler job) does:
```
Thread(target=_run_aiem_continuous_research, daemon=True).start()
record_job_success("aiem_continuous_research")
```
`record_job_success` fires right after `.start()`, not after the thread finishes. The `job_heartbeats` row will show `consecutive_failures=0` and a fresh `last_success` timestamp even if the background function immediately crashes, finds nothing, or silently swallows an exception in its own internal try/except.

Contrast: aiem_autonomous.py's `_logged_job` decorator calls the job function synchronously and logs to `job_log` in a `finally` block after the call returns/raises — much closer to real completion evidence (though it doesn't distinguish success from failure either, since finally runs on exceptions too).

**Why:** Discovered while auditing whether the continuous-research hypothesis loop had ever saved a real finding. `aiem_research_insights` had zero rows tagged from that loop despite one "successful" heartbeat — the heartbeat was worthless as completion proof; had to cross-check the actual output table instead.

**How to apply:** Before treating any `job_heartbeats`/heartbeat-table "success" row as proof a scheduled job did real work, check whether the job is spawning a bg thread/process and firing the heartbeat before or after that work. If before, verify via the job's actual DB output/side-effect table instead, and treat "0 consecutive_failures" as meaning "was invoked", not "produced results".
