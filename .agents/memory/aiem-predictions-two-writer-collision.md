---
name: aiem_predictions two-writer collision + read-only notifier pattern
description: Why aiem_autonomous.py must not be deployed as-is alongside main.py's aiem_predictions writer, and the read-only-notifier fix used instead.
---

`aiem_autonomous.py` is not just a Telegram sender — its `aiem_premarket_scan()` does
`DELETE FROM aiem_predictions WHERE prediction_date = ...` then re-INSERTs, on a schedule
(every 15 min, 7:00-9:30 AM ET). `main.py`'s `_run_aiem_morning_scan()` (9:05 AM ET) writes
the same table for the same `prediction_date`. `aiem_standalone_scanner.py` is a *third*
independent writer to the same table (07:00 AM). Deploying more than one of these to
production at once is a silent race: whichever job runs last for the day wins, discarding
the other's predictions with no error.

`aiem_autonomous.py`'s own `aiem_morning_brief()` (8 AM) and `aiem_missed_morning_check()`
also have baked-in fallback logic that *triggers* `aiem_premarket_scan()` (a write) whenever
they find zero rows — so even "just running the notifier function" isn't read-only unless
that fallback is removed.

**Why:** the user's core complaint (picks never reached Telegram) was a delivery problem,
not a data problem — `main.py` already had a correct (post-fix) writer. Deploying the whole
of `aiem_autonomous.py` to "fix delivery" would have reintroduced a *worse*, silent
data-collision bug on top of the one just fixed.

**How to apply:** when asked to make an AIEM Telegram alert reach production, do NOT deploy
`aiem_autonomous.py` verbatim. Instead extract a minimal, genuinely read-only notifier
(see `aiem_telegram_notifier.py` at repo root) that only SELECTs from `aiem_predictions`
(main.py stays the sole writer) and fails closed (sends a "data not ready" message, does not
scan) when no rows exist. Schedule it *after* the canonical writer's time, with a buffer
(main.py writes 9:05 AM ET -> notifier sends 9:15 AM ET). Treat `aiem_standalone_scanner.py`
as permanently out of scope for production deployment unless the multi-writer race is
resolved project-wide first (single canonical writer + read-only consumers everywhere).

Separately: `aiem_autonomous.py` hardcodes its health-check port default to 5051
(`AIEM_HEALTH_PORT` env, default `'5051'`) and that dev workflow is often already running
live in the same container — any new service sharing that literal port number will collide
even though artifact.toml scopes env vars "per service" (they still share one OS network
namespace in dev). Pick a genuinely different literal port for sibling AIEM services.
