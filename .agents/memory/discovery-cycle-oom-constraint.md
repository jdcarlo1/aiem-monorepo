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

## Verified
2026-07-25: admin-trigger with full constants → crash after 6min. Standalone 6-month split → 47.11s, total_templates=10, clean completion.
