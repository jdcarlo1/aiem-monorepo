---
name: Decision logging wiring
description: How agent_decisions audit trail is wired into the four main signal modules; critical API constraints and a pre-existing SQL bug that was fixed.
---

## The rule
`dl.log_decision(signal_name, decision_type, reasoning, ticker=None, ...)` — `decision_type` MUST be one of `"trade"`, `"no_trade"`, `"hold"`, `"exit"`. Passing anything else (e.g. `"signal_fire"`) raises `ValueError` and the insert never happens.

**Why:** The spec suggested `"signal_fire"` as decision_type, but the actual decision_logger.py enforces a strict enum. Signal fires map to `"trade"`.

## Wiring locations in main.py
- `gamma_pressure_scan` — inside `_scan_one()` in `_run_gamma_pressure_scan()`, right after `if fir < 1.2: return None`, before `return {...}`.
- `charm_cascade` — inside `_get_charm_cascade_signals()`, after `rows = cur.fetchall()` and outside the `with` block, iterating all returned rows.
- `dark_pool_scanner` — inside `_get_dark_pool_convergence()`, after `result[ticker] = {...}`, gated at `off_exchange_pct >= 45`.
- `unusual_calls_scanner` — inside `_run_unusual_calls_scan()`, after `_save_unusual_calls_to_db(all_hits)`, logging top 5 by premium.

All calls go through `decision_logging_helper.py` (in `artifacts/stock-scanner-api/`). Every helper function wraps `dl.log_decision()` in `try/except` so a logging failure never propagates into the scanner.

## Pre-existing SQL bug fixed alongside
`_get_charm_cascade_signals()` was silently returning `[]` on every call because:
```sql
ROUND((oi * 100.0 * ...) / (...), 1)
```
`oi` is stored as `double precision` in `oi_daily_snapshot`. PostgreSQL has no `ROUND(float8, integer)` function — only `ROUND(numeric, integer)`. Fix: add `::numeric` cast:
```sql
ROUND(((oi * 100.0 * ...) / (...))::numeric, 1)
```
The outer `except Exception: return []` caught this silently. Charm scores in the conviction stack were always missing due to this bug.

## Verification
- charm_cascade: 20 rows, 20 distinct reasoning strings (perfect 1:1)
- dark_pool_scanner: 39 rows, 15 distinct reasoning strings
- gamma + unusual_calls: wired (grep confirmed), 0 rows — market closed (Sunday), these only fire during market hours
