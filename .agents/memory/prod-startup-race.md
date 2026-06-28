---
name: Prod startup race condition — deferred DB init pattern
description: Why prod timed out before port 5050 bound, how it was found, and the fix pattern for any future init functions.
---

# Root cause
48 module-level `_init_*_table()` calls each opened their own `psycopg2.connect()` at import time. Dev (no SSL): ~2ms each = 0.11s total. Prod (SSL cold start): ~300–1000ms each × 48 = 14–48s serial. Replit healthcheck fires SIGTERM at T+35.5s; Flask never got to `app.run()`.

**Why logs were useless:** Python stdout was BUFFERED in the prod container. ALL startup log lines flushed at the same millisecond (SIGKILL). The stall was completely invisible. Fix: `sys.stdout.reconfigure(line_buffering=True)` added at line 2 of main.py.

# Fix pattern
Replace every `func()` at module level with:
```python
_DEFERRED_INITS.append(lambda: func())
```
Then before `app.run()`, start a daemon thread that runs them all:
```python
def _run_deferred_inits():
    for fn in _DEFERRED_INITS:
        try: fn()
        except Exception as e: print(f"[startup-init] error: {e}")
    print(f"[startup-init] {len(_DEFERRED_INITS)} done in {time.time()-t0:.2f}s")

threading.Thread(target=_run_deferred_inits, daemon=True).start()
```
This lets `app.run()` bind the port immediately (T+~9s dev, T+~12s prod) while inits run in background (all tables already exist from previous boots, so CREATE TABLE IF NOT EXISTS is a no-op in ~0.09s).

**Why:** Tables already exist on every restart after first boot. The init functions are CREATE TABLE IF NOT EXISTS guards — they can safely run after port bind with zero risk.

**How to apply:** Any new `_init_*()` function called at module level MUST use `_DEFERRED_INITS.append(lambda: _init_new_table())` — never a bare call.
