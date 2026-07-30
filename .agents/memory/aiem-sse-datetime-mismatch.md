---
name: aiem_sse naive/aware datetime mismatch
description: job_heartbeats columns are naive TIMESTAMP; aiem_sse_poller_state.last_seen_ts is TIMESTAMPTZ (aware); comparison raises TypeError on every poll cycle after the first.
---

## The rule
When reading from `job_heartbeats` (columns `last_success`, `last_attempt` are `TIMESTAMP WITHOUT TIME ZONE` → psycopg2 returns naive datetimes) and comparing against `aiem_sse_poller_state.last_seen_ts` (`TIMESTAMPTZ` → psycopg2 returns aware datetimes), always normalize the naive side before comparison.

**Why:** PostgreSQL returns naive datetimes for `TIMESTAMP` columns and aware datetimes for `TIMESTAMPTZ` columns. Python raises `TypeError: can't compare offset-naive and offset-aware datetimes` on any `>` / `<` comparison between the two. This manifests on the SECOND and all subsequent poll cycles, because the first cycle has `last_ts = None` (no row in state table yet) so the comparison is bypassed.

**How to apply:**
```python
r_success = row[1].replace(tzinfo=timezone.utc) if row[1] and row[1].tzinfo is None else row[1]
r_attempt  = row[2].replace(tzinfo=timezone.utc) if row[2] and row[2].tzinfo is None else row[2]
```
This is identical to the pattern already in `aiem_watchdog.py` line 142. Any new code that reads `job_heartbeats` timestamps and compares them to a TIMESTAMPTZ must apply this normalization.

## Fixed locations
- `aiem_sse.py` — `_poll_system_health()` lines 329–332. Commit `024889fc` (2026-07-22). SHA before: `c9d610f9`, after: `24822922`. SEQ=117 (blame, chain SEQ=117).
- `aiem_watchdog.py` — `check_vm_heartbeat()` lines 140–144. Introduced by `931dc8efb81bf3a60020a2f553def60f77bae7a3` (2026-07-17, file-creation commit "Add an independent backup system…"). SHA at creation: `5f211240`. `59d887b` is a later modification to the same file (recovery-check logic) and did NOT introduce these lines — confirmed by `git blame` at chain SEQ=116.

## Negative controls (confirmed 2026-07-30)
- aiem_watchdog.py: synthetic row last_success=60 min ago → `is_stale=True`, age_min=60.0. Rolled back, row_count=0.
- aiem_sse.py: synthetic row last_success=90 min ago → `is_stale=True`, age_min=90.0. Rolled back, row_count=0.
