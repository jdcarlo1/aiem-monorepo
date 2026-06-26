---
name: Flask tab hang root causes
description: Three layered causes of "every tab spins at market open" — import lock, pool lock no-timeout, bg thread storm; and their fixes.
---

# Problem
StockScanner AI: all 15 dashboard tabs returned 0 bytes / spinner at market open on prod.

# Root Cause Chain (in order of impact)

## 1. Scheduler burst (T001–T002)
APScheduler default config (max_workers=10, coalesce=False, misfire_grace_time=1s) launched all morning jobs simultaneously at 9:30–9:45 ET, saturating CPU + yfinance budget.

**Fix:** `max_workers=4`, `coalesce=True`, `max_instances=1`, `misfire_grace_time=600`. Stagger morning scan slots across the window.

## 2. Synchronous live scans in request handlers (T003)
Many tab endpoints did live yfinance fetches inline. When Yahoo throttled (429/401), they hung 18s+.

**Fix:** Yahoo circuit breaker (`_yahoo_breaker`): trip on 429/401, fail fast, serve cache/empty. All endpoints must check `_yf_breaker_open()` before any live fetch.

## 3. 4 heavy endpoints blocking Flask workers (T005-a)
conviction-stack, ai-short-calls, insider-radar, insider/trades each did heavy live scans or OpenAI calls synchronously.

**Fix:** Background thread pattern — handler returns `{"generating": True}` immediately; `_bg_*` thread fills `app._*_cache`; subsequent requests serve cache with `{"stale": True}`.
- `_bg_stk` and `_bg_ir` have 60s/30s startup delays to stagger boot load.

## 4. Python global import lock (T005-b — the subtle one)
`_bg_aisc()` ran `from openai import OpenAI` as the FIRST-EVER import of openai in the process. Python's import system holds a global lock during module loading (2-5s for openai). Flask request threads doing ANY inline `import …` (e.g., `import psycopg2 as _pg_st` in standout-track) blocked until the openai import completed.

**Symptom:** standout-track + gap-volume-signal returned 0 bytes for exactly the duration of the openai import, worked perfectly in isolation.

**Fix:** Pre-import heavy modules at module level:
```python
try:
    from openai import OpenAI as _OpenAI  # noqa: F401
    from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: F401
    import concurrent.futures as _concurrent_futures  # noqa: F401
except Exception as _preload_err:
    print(f"[preload] {_preload_err}")
```

**Rule:** Any module imported for the FIRST TIME inside a background thread holds the global import lock. Flask request threads doing any `import` statement will block. Pre-import all heavy libraries at module load time.

## 5. DB pool lock with no timeout (T005-c)
`ThreadedConnectionPool.getconn()` uses `self._lock.acquire()` with no timeout. If a background thread holds the lock while doing a slow TCP `_connect()` (creating a new socket to DB), Flask request threads calling `getconn()` blocked indefinitely.

**Fix:** Replace `_pg_pooled_connect` to use explicit lock acquisition with timeout:
```python
def _pg_pooled_connect(*_a, **_kw):
    _timeout = float(_kw.pop("connect_timeout", None) or 5)
    if not _PG_POOL._lock.acquire(blocking=True, timeout=_timeout):
        raise Exception(f"[db] pool lock busy >{_timeout}s")
    try:
        _raw = _PG_POOL._getconn()
    except Exception:
        _PG_POOL._lock.release()
        raise
    _PG_POOL._lock.release()
    return _PoolConn(_raw, _PG_POOL)
```

**Why:** psycopg2's `ThreadedConnectionPool` calls `self._connect()` (TCP handshake) while holding `_lock`, so any concurrent `getconn()` waits the full TCP connect duration. The `connect_timeout` kwarg passed to `psycopg2.connect()` is ignored by the pool shim — must be implemented via the lock timeout.

# Result
15/15 tabs return 200 in <800ms with all background threads running concurrently.

# Key Rules
1. Never do first-time heavy imports inside background threads — pre-import at module load.
2. Always give the pool lock a timeout — `connect_timeout` kwarg alone is not enough.
3. Never do live yfinance/network fetches synchronously in Flask request handlers — bg thread + cache pattern.
4. Startup bg threads must be staggered (use `_BOOT_TIME` guard) to avoid simultaneous pool pressure.
