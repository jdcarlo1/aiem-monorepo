---
name: startup_preload initialization order
description: startup_preload thread starts before module-level aliases are defined; must be fully self-contained
---

## Rule
`_startup_preload` is launched as a daemon thread early in module loading — before the module-level aliases `_psycopg2` (line ~10330) and `_DB_URL` (set later) exist. Any name it references must be imported or computed locally inside the function body.

**Why:** The old `time.sleep(5)` at the top of the function was accidentally masking this — the 5-second delay let the module finish loading before the thread actually ran. When the sleep was removed (to fix empty tabs on restart), the race became visible: every tab failed with `name '_psycopg2' is not defined` and then `name '_DB_URL' is not defined`.

**How to apply:**
```python
def _startup_preload():
    import time as _t_pl, datetime as _dt_pl
    import psycopg2 as _psycopg2, os as _os_pl   # ← REQUIRED local imports
    _DB_URL = _os_pl.environ["DATABASE_URL"]       # ← read from env, not module alias
    ...
```
Never remove these local imports or "clean them up" — they are not redundant.

## Also applied: atomic swap + is_ready pattern (conviction-stack)
The conviction-stack endpoint now returns HTTP 202 with `{"loading": True, "note": "..."}` instead of a silent empty 200 when no cache is available. This prevents the UI from showing "no stocks found" during warmup.
