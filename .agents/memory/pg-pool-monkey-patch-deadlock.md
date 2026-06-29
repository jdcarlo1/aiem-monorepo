---
name: psycopg2 pool + monkey-patch deadlock
description: Three bugs that arise when you replace psycopg2.connect with a pool wrapper after the pool is already created.
---

## The Three Bugs

### Bug 1 — Self-deadlock (the primary outage)
`psycopg2.pool.ThreadedConnectionPool._connect()` calls `psycopg2.connect()` **while holding `_PG_POOL._lock`** (non-reentrant).
If `psycopg2.connect` has been replaced with the pool wrapper (`_pg_pooled_connect → _PG_POOL.getconn() → _lock.acquire()`), the same thread self-deadlocks. Every other thread queues behind it. All tabs hang.

**Trigger:** only when the pool needs to GROW beyond `minconn` (both pre-warmed connections in use simultaneously — happens at T+180s when concurrent scans start).

**Fix:** save `_pg2_orig_connect = _psycopg2.connect` BEFORE patching, then override `_PG_POOL._connect` instance method to use it:
```python
_pg2_orig_connect = _psycopg2.connect   # BEFORE patch
_PG_POOL = ThreadedConnectionPool(...)
def _pool_direct_connect(pool_self, key=None):
    conn = _pg2_orig_connect(*pool_self._args, **pool_self._kwargs)
    if key is not None:
        pool_self._used[key] = conn
        pool_self._rused[id(conn)] = key
    else:
        pool_self._pool.append(conn)
    return conn
_PG_POOL._connect = types.MethodType(_pool_direct_connect, _PG_POOL)
_psycopg2.connect = _pg_pooled_connect  # AFTER
```

### Bug 2 — "the connection cannot be re-entered recursively" (psycopg2 ≥2.9)
`_pg_pooled_connect` calls `_raw.cursor().execute("SELECT 1")` as a health-check before returning the connection. This leaves the connection in `STATUS_IN_TRANSACTION`. The caller does `with psycopg2.connect(...) as conn:` which calls `conn.__enter__()` → `_raw.__enter__()`. psycopg2 ≥2.9 raises the error if `STATUS_IN_TRANSACTION`.

**Fix:** `_raw.rollback()` immediately after the health-check to reset to idle.

### Bug 3 — Double-use (same raw conn given to two callers)
`_pool_direct_connect` initially did `pool_self._pool.append(conn)` unconditionally. psycopg2's real `_connect` appends to `_pool` ONLY when `key is None`. For auto-keyed connections (`key != None`), it goes to `_used/rused`. The pool uses auto-generated keys for all `getconn()` calls without explicit key. Appending unconditionally made the conn appear "available" while already in use → second caller got same raw conn → `__enter__` called twice → re-entry error.

**Fix:** mirror real psycopg2 `_connect` exactly: `key is not None → _used/rused`, else `_pool.append`.

## Key facts about psycopg2 ThreadedConnectionPool
- `getconn()` (no key): internally assigns auto-key via `_getkey()`, tracks in `_used`
- `putconn(conn)` (no key): looks up key from `_rused[id(conn)]`, removes from `_used`, appends to `_pool`
- `_connect(key)` is called WHILE `_lock` is held — never call anything that tries to re-acquire it

**Why:**
The outage caused every tab to hang for 6s+ at market open. minconn=2 is fine in dev but in production with 10+ concurrent scans it's exhausted in seconds every morning. The deadlock was permanent (non-reentrant threading.Lock held by its own thread).

**How to apply:**
Any time you monkey-patch `psycopg2.connect` to route to a pool, always save the original first and override `pool._connect` on the instance. Never patch before creating the pool.
