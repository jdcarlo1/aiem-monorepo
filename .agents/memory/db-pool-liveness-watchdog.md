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

## Second incident (2026-07-09): the watchdog assumption above was wrong
The prior watchdog design assumed it only needed to catch *total* wedge (health route itself
hangs). A second, distinct recurring daily full-VM outage (again both co-located artifacts down,
same "TLS connects, zero HTTP response" symptom) proved that assumption insufficient: a process can
keep answering its own health check just fine while it slowly starves the whole Reserved VM out
from under every co-located artifact (NCLEX Prep + stock-scanner share one VM).

**Root cause:** ~540+ ad-hoc `psycopg2.connect()` call sites across 4 separate processes
(main.py, aiem_process.py, aiem_telegram_notifier.py, aiem_probability_engine/daily_scheduler.py)
with no TCP keepalives. When the DB's TCP path dies silently (no clean FIN/RST — routinely visible
as "SSL connection has been closed unexpectedly" in logs), a raw `connect()`/`recv()` with no
keepalives can block a thread forever. Background jobs re-spawn threads each tick without joining
previously-stuck ones, so OS threads/memory leak unbounded until the VM itself starves. Separately,
found (and fixed) 4 APScheduler jobs registered as `lambda: threading.Thread(target=fn,
daemon=True).start()` — this defeats `max_instances=1` because the lambda returns instantly while
the real work runs unguarded on a detached thread, so a hung run doesn't block the next scheduled
run from also starting.

**Fix:** (1) global `psycopg2.connect` monkey-patch adding
`keepalives=1/keepalives_idle=10/keepalives_interval=5/keepalives_count=3/tcp_user_timeout=30000`
via `setdefault`, applied once near the top of each of the 4 process entry points — covers every
call site in that process since `psycopg2.connect` is a module attribute resolved at call time, not
import time (works even for files imported later, as long as none use
`from psycopg2 import connect`). (2) non-blocking `threading.Lock` guard (acquire/skip-print/release
in `finally`) added to the 2 of 4 lambda-thread scheduler jobs that lacked one (the codebase already
had this convention elsewhere — check for it before assuming a scheduler job is unprotected).
(3) `_liveness_watchdog_loop` (main.py) now ALSO logs `threading.active_count()` +
`/proc/self/status` RSS + `/proc/meminfo`-derived RSS% every 30s cycle and force-exits above 400
threads or 70% RSS — catching the starvation building up *before* the process is fully wedged or
OOM-killed, which the health-check-only design could not.

**Residual gap, surfaced to user, not fixed:** the watchdog only runs inside main.py's process;
the other 3 processes have no resource watchdog of their own, and aggregate multi-process memory
could theoretically starve the VM without any single process crossing its own threshold. Also,
co-locating NCLEX Prep and stock-scanner on one Reserved VM means a stock-scanner-side leak can
still take down an unrelated product — the only true fix for that is splitting them into separate
deployments, which is an infra decision that needs user buy-in, not something to do unilaterally.

**Lesson:** "the liveness watchdog will catch a full wedge" is not the same claim as "the watchdog
prevents VM starvation" — a slow leak answers health checks fine right up until it doesn't. Any
future watchdog design for this class of problem needs a resource-trend check (threads/RSS), not
just a liveness check, and needs to run in every process that can leak, not just the primary one.
