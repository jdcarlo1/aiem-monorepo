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

## Fixed location
`artifacts/stock-scanner-api/aiem_sse.py` — `_poll_system_health()`. SHA before: `c9d610f9`, after: `24822922`. SEQ=80, 7 PASS / 0 FAIL, 9/9 PSV. Verifier: `tools/verify_d22a_sse_datetime.sh`.

## Negative control
`aiem_watchdog.py` line 142 already had the correct `.replace(tzinfo=timezone.utc) if ... .tzinfo is None else` guard. No other file does a bare naive/aware comparison against `job_heartbeats` timestamps.
