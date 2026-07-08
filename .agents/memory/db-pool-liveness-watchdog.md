---
name: DB pool finalizer deadlock + liveness watchdog
description: Root cause and fix for stock-scanner prod going fully unresponsive (~daily); relevant when debugging Reserved VM freezes, "TLS connects but zero HTTP response", or any future production hang in artifacts/stock-scanner-api
---

## The bug
`_PoolConn.__del__` called `self._put_back()` → `psycopg2.pool.putconn()`. `__del__` runs as a
GC finalizer on **whatever thread happens to trigger garbage collection**, which can be a thread
that already holds the pool's internal (non-reentrant) lock for a *different* connection (mid
`getconn()`/`putconn()`). Re-entering that lock from the finalizer deadlocks forever. Since every
DB call in the app shares one pool/lock, this one deadlock eventually freezes the entire app with
no crash or traceback logged — matching the observed symptom (TLS handshake succeeds, zero HTTP
response, `google.com` fine, only this app dead).

**Why it took a while to find:** no error is ever raised or logged — the process just silently
stops answering requests as blocked threads pile up. A restart "fixes" it, which made it look like
a transient host issue rather than a lock-ordering bug in the app itself.

## The fix (3 parts, in `artifacts/stock-scanner-api/main.py`)
1. `_PoolConn.__del__` never calls back into the pool. It closes the raw psycopg2 connection
   directly (`self.__dict__["_conn"].close()`) and logs a WARNING that a connection leaked without
   an explicit `close()`. A `_returned` guard makes this a no-op after normal close.
2. `connect_timeout=5` added to `ThreadedConnectionPool(...)` to bound DNS/connect stalls during
   pool growth while the lock is held.
3. A `_liveness_watchdog_loop` daemon thread self-checks a DB-free health route every 30s; after 3
   consecutive failures/timeouts it force-exits (`os._exit(1)`) so the Reserved VM supervisor
   restarts the process automatically instead of requiring a manual republish.

**Why the watchdog probe must stay DB-free:** if it touched the pool it could hang on the exact
same lock, defeating the point. Trade-off (accepted, confirmed by architect review): it only
detects *total* wedge (all worker threads blocked), not a partial DB-only freeze, since werkzeug
spawns a fresh thread per request and the health route itself never touches the DB.

## Known residual (non-blocking, monitor only)
A leaked connection that gets closed directly in `__del__` is never removed from the pool's
internal `_used`/`_rused` bookkeeping, so each leak permanently eats one of the 25 `maxconn` slots.
This degrades into `PoolError: pool exhausted` (a loud error, not a silent freeze) if leaks
recur. **Do not add a reaper thread preemptively** — only if the
`[db] WARNING: _PoolConn garbage-collected without close()` log line actually starts appearing in
prod. Fix at that point: `__del__` pushes the raw conn onto a `queue.Queue`, a normal (non-finalizer)
daemon thread drains it calling `_PG_POOL.putconn(conn, close=True)`.

## Related, ruled out
`staleness_guard.py` (file-mtime/git-SHA watchdog that does `os.execv` restarts) was investigated
as a possible trigger and refuted (no CRITICAL banner before the freeze in logs). It's still
unnecessary risk in prod, so it's now gated behind `REPLIT_DEPLOYMENT != "1"` and only runs in dev.
