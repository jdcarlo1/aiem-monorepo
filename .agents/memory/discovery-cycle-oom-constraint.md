---
name: Discovery Cycle OOM — streaming fix and production status
description: Root cause, kill switch state, and server-side cursor fix for the discovery cycle OOM crash.
---

## Root cause
`_load_backtest_universe` called `cur.fetchall()` loading 3M rows as Python dicts (~1.2GB), spiking Flask RSS to 2826MB (rss_pct=72.1%) and vm_pressure to 97.5% on the 4GB production VM. Both watchdog gates (rss_pct>70%, vm_pressure>96%) fired and Flask called os._exit(1).

## Kill switch
`discovery_cycle_config SET value='false' WHERE key='enabled'` — ACTIVE as of 2026-07-31.
Do NOT re-enable without explicit user (Joel) approval after reviewing dev proof.
Kill switch check is at `main.py:3099–3103`: `if enabled != "true": return`.

## Fix implemented (server-side cursor streaming)
`aiem_discovery_engine.py` — SHA b0831832 (post-fix, 1328 lines)

**New functions added:**
- `_BACKTEST_WINDOW_SQL` — module-level SQL constant (extracted from `_load_backtest_universe`)
- `_stream_backtest_universe(start, end, timeout_ms=900_000, batch_size=10_000)` — named psycopg2 cursor generator; streams in 10k-row batches without materialising full result set
- `_make_accumulators(templates)` — per-template running tallies
- `_update_accumulators(accum, batch, templates)` — accumulates stats from one batch (matches `_compute_stats` semantics exactly)
- `_finalize_stats(accum, templates)` — converts accumulators to stats dicts (same structure as `_compute_stats` output)

**Changed:**
- `_evaluate(template, is_stats, oos_stats)` — now accepts pre-computed stats dicts, not raw row lists
- `run_cycle()` — replaced single `_load_data()` call (3M row fetchall) with two streaming passes (train → test), accumulator tallies, then finalize + evaluate

**Unchanged:** `_load_backtest_universe` (kept for `post_backfill_evidence.py` tool), training/test window dates, template logic, all WL cycle code.

## Dev proof (2026-07-31, dev 8GB VM)
- Baseline: flask_rss=628MB, vm_pressure=58.1%, vm_avail=3335MB
- Peak child_rss: **127.4MB** (was 2520MB+ before fix — 20x reduction)
- Peak vm_pressure: **60.4%** (delta 2.3pp from baseline; threshold is 96%)
- vm_avail minimum: 3,152MB (dropped only 183MB during the cycle)
- Child completed: rc=0 in **150.1 seconds**
- Rows processed: 1,326,644 train + 1,713,129 test = 3,039,773 total
- Flask RSS throughout: 626–637MB (completely stable)

## Production projection (4GB VM)
- Production baseline vm_avail = 1,718MB (measured from prod DB at 21:29 UTC 7/31)
- Expected vm_avail drop during streaming cycle = ~183MB (from dev observation)
- Projected vm_avail during cycle = 1,718 - 183 = 1,535MB
- Projected vm_pressure = (3923 - 1535) / 3923 = 60.9% — well below 96% Gate 2
- Flask rss_pct stays at ~31.8% (baseline, no growth) — well below 70% Gate 1

## Status (as of 2026-07-31)
Kill switch: ACTIVE (value='false').
Dev test: PASSED.
Awaiting Joel review before: lifting kill switch, deploying to production, running prod cycle.

## Option B (backfill rvol) — NOT a fix
gap_pct is 99.6% filled on production (backfill already ran successfully).
rvol is 0% filled for months before 2026-04 on production (backfill timed out for old dates).
Completing the rvol backfill would make the SQL faster but returns the same number of rows to Python — it does NOT reduce the Python fetchall memory spike. Never claim Option B fixes the OOM.
