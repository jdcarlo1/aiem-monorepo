---
name: Decimal * float TypeError silently killed the open-watcher alert
description: psycopg2 returns NUMERIC DB columns as decimal.Decimal; mixing that with a plain float in arithmetic raises TypeError and can silently abort a whole scoring loop before any alert is sent.
---

## The bug
`aiem_process.py`'s `aiem_open_watcher()` blends a premarket confidence score (`base_conf`, read from a `NUMERIC` column via psycopg2 → `decimal.Decimal`) with a live-rescored float (`live_conf`) via `base_conf * 0.4 + live_conf * 0.6`. `Decimal * float` raises `TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'`, caught by the function's outer `except Exception` and logged as `open_watcher error: ...` — no crash of the process, no alert, no visible symptom besides one log line easy to miss.

This meant the daily "S1B·S1C·S1D Morning Picks" Telegram alert would crash (and thus never send) on ANY day where live price data was actually available (Tradier/Polygon fallback succeeded) — i.e. it only had a chance of working on days where live data was unavailable and it fell back to the premarket-score-only path (which does `float(base_conf)` correctly). Combined with a missing production service registration (see `artifact-toml-service-registration-gap.md`), this is why the feature had *never* successfully alerted.

**Why:** any DB column typed `NUMERIC`/`DECIMAL` comes back from psycopg2 as `decimal.Decimal`, not `float`. Decimal refuses silent coercion in arithmetic with float (unlike int).

**How to apply:** whenever blending/scoring code mixes a value pulled from a NUMERIC DB column with a computed Python float, wrap the DB-sourced value in `float(...)` explicitly at the point of arithmetic — don't assume "it's just a number." Grep for other `* 0.` / `+ ` arithmetic on raw DB row values in the same codebase if a similar silent-failure pattern is suspected elsewhere.
