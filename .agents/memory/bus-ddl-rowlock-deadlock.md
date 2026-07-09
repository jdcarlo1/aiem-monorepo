---
name: Bus DDL / RowExclusiveLock deadlock
description: Python-level deadlock when _cu INSERT (inside open transaction) holds RowExclusiveLock on aiem_bus_transfer_log while execute_stage() calls _ensure_schema() CREATE INDEX (ShareLock) on the same table.
---

## The Rule
Call `_abus.get_bus()` (or any `_ensure_schema()` equivalent) **once, before** the first `_cu INSERT` that touches `aiem_bus_transfer_log` inside an open psycopg2 transaction.

## Why
PostgreSQL lock hierarchy:
- `INSERT` holds `RowExclusiveLock` on the target table (via `_cu`, inside outer `_c` transaction)
- `CREATE INDEX` requires `ShareLock` on the same table (`_ensure_schema()` inside `execute_stage()`)
- `ShareLock` conflicts with `RowExclusiveLock` → CREATE INDEX blocks waiting for the outer transaction to commit
- Outer transaction can't commit because it's waiting for `execute_stage()` to return
- Python-level deadlock: no automatic PostgreSQL deadlock detection (only one waiter is a PG lock)

## How to Apply
In any MTM/close path that:
1. Opens a long-lived psycopg2 connection `_c` (one big transaction for the whole trade close)
2. Uses `_cu INSERT INTO aiem_bus_transfer_log` inside that transaction
3. Also calls `execute_stage()` (which internally calls `get_bus()` → `_ensure_schema()`)

**Fix pattern** — add this once, before the first `_cu` INSERT into the bus table:
```python
import aiem_communication_bus as _abus_pw
_abus_pw.get_bus()   # runs CREATE TABLE/INDEX DDL now, sets _DB_INIT_DONE=True
```
After this call, all subsequent `get_bus()` calls skip DDL entirely. Lock contention is gone.

## Evidence
Verified on trace `prewarm_fix_trace_v1` (AAPL, 2026-07-09):
- 8 bus rows (stage_starting + stage_completed for s=20/21/22/23)
- 4 PASS trace-audit rows
- Trade correctly CLOSED_EXPIRED

Without the fix: MTM blocks indefinitely at stage 20's `_ensure_schema()` CREATE INDEX; `_c.commit()` never runs; trade stays OPEN.
