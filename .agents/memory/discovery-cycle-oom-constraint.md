---
name: Discovery cycle production OOM constraint
description: Full-year windows (2024-07-22→today) in _load_backtest_universe OOM the production VM when run via admin endpoint; memory-safe alternatives and root cause.
---

## Rule
The AIEM discovery cycle with `_TRAIN_START="2024-07-22"` through today (~3M rows via the on-the-fly COALESCE approach) cannot complete inside the Flask stock-api process on the production VM without triggering the liveness watchdog.

## Why
- Full windows: train ~1.3M rows + test ~1.7M rows = ~3M Python dicts × ~400 bytes ≈ 1.2 GB additional heap
- Production VM baseline memory pressure: 79.6% (`rss_mb=606.8` for stock-api alone)
- Liveness watchdog kills after 3 consecutive health-check failures (~90s per check cycle)
- The cycle never writes a completed row to `discovery_cycle_log` — it just gets force-killed

## How to apply
- **Standalone process** (no Flask overhead): 6-month split (2025-01-01→2025-06-30 train, 2025-07-01→2025-12-31 test) = ~1.5M rows, completes in ~47s, no OOM
- **Option B (stored UPDATE backfill)**: Once `gap_pct`/`rvol` are stored, COALESCE short-circuits for those rows; full 2-year window runs in <5s; OOM eliminated
- **Admin-trigger cycles**: Only safe with narrower windows (< ~1.5M rows total) or after Option B backfill
- The scheduled 2AM nightly cycle is also likely to OOM with full-year windows on this VM

## Critical governance rule
Never commit reduced/hardcoded date-window constants without Joel's explicit approval. A hardcoded past `_TEST_END` is a functional regression (OOS validation permanently stale), not a memory fix. `_TEST_END` must always be a rolling expression (`_de_dt.date.today().isoformat()`), not a literal string.

## Fix implemented (2026-07-31)
**Subprocess isolation** — `_discovery_cycle_job` in main.py now spawns `run_discovery_cycle_subprocess.py` as a child process instead of calling `run_cycle()` inline. The child owns all memory-heavy work (run_cycle + run_tiered_wl_cycle); the parent reads the result from `/tmp/dc_result_{run_id}.json` and runs M3–M7 (lightweight). If the child OOMs, the kernel kills it; Flask is unaffected. Dev restart confirmed clean load.

**What still runs in parent (main.py):** Module 2 Thompson ranking (DB query, no row data), M3 SGD update, M4 adversarial critique, M5 promotion check, M7 feedback loop, discovery_cycle_log update, Telegram M8 alerts.

**What runs in subprocess:** `run_cycle()` (loads train+test universes), `run_tiered_wl_cycle()`.

## Verified
2026-07-25: admin-trigger with full constants → crash after 6min. Standalone 6-month split → 47.11s, total_templates=10, clean completion.
2026-07-31: Production OOM crash confirmed (RSS 2640MB, vm_pressure=93.3% at 21:31 UTC). Subprocess fix deployed to dev — stock-api restarted cleanly, no errors.
