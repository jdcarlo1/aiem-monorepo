---
name: Discovery engine on-the-fly COALESCE pattern
description: polygon_market_daily UPDATE permanently blocked by Flask pool lock; read-only COALESCE+LAG/AVG is correct fix; run_status="aborted_no_data" prevents silent log nulls
---

## The Rule
Never attempt `UPDATE polygon_market_daily` while the Flask stock-api is running.
The connection pool holds a persistent `RowExclusiveLock` that never releases between requests.
Every UPDATE attempt hits `lock_timeout` regardless of duration (tested up to 90s).

**Why:** psycopg2 connection pooling keeps idle connections in a transaction-ready state that holds implicit row locks on recently-written tables. The only safe window is the 3 AM nightly reset when all workflows exit.

## How to Apply
When `_load_backtest_universe` needs gap_pct/rvol for historical rows with NULL stored values, use a 3-CTE structure:

```sql
WITH source AS (
    -- Fetch [buf_start, end] where buf_start = start - 45 days
    -- Compute LAG(close_price) and AVG(volume) OVER 30 PRECEDING
    SELECT ..., LAG(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS prev_close_computed,
           AVG(volume) OVER (...30 PRECEDING...) AS avg_vol_30
    FROM polygon_market_daily WHERE scan_date BETWEEN %s AND %s
),
derived AS (
    -- Filter to actual [start, end] and COALESCE stored vs computed
    SELECT ...,
           COALESCE(gap_pct, (open_price/prev_close_computed)-1.0) AS gap_pct,
           COALESCE(rvol, volume::numeric/avg_vol_30) AS rvol
    FROM source WHERE scan_date BETWEEN %s AND %s
),
windowed AS (
    -- LEAD(close_price) for next-day outcome
    SELECT ..., LEAD(close_price) OVER (...) AS next_close
    FROM derived WHERE gap_pct IS NOT NULL AND rvol IS NOT NULL AND ...
)
SELECT ... FROM windowed WHERE next_close IS NOT NULL ...
```

This is pure SELECT — no RowExclusiveLock contention — and runs in 50-80s for 1-year windows.

## Silent-Failure Pattern Fix
`run_cycle()` early-return (no data loaded) previously returned `{"error": "...", "train_n": 0, "test_n": 0}` with no `run_status` key. `_discovery_cycle_job` only populated `discovery_cycle_log.error_msg` on raised exceptions — so early-return wrote `error_msg=NULL`, identical to a legitimate clean-zero run.

**Fix:** Early-return dict now includes `"run_status": "aborted_no_data"`. `_discovery_cycle_job` checks `result.get("run_status") == "aborted_no_data"` and propagates `error_msg` from the result dict.

Distinguishing states in `discovery_cycle_log`:
- **Abort (no data):** `error_msg` populated, `total_templates=0`
- **Clean zero (data loaded, no templates pass):** `error_msg=NULL`, `total_templates>0`
- **Exception:** `error_msg` populated, `total_templates=0`

## Qualifying Row Counts (2026-07-25 baseline)
- Train 2024-07-22 → 2025-06-30: **1,326,644** rows
- Test  2025-07-01 → 2026-07-22: **1,681,282** rows

## Backfill Script
`artifacts/stock-scanner-api/tools/backfill_gap_rvol.py` — runs UPDATE at 3 AM when pool releases locks. Now **performance-only** (avoids window-function overhead for already-computed rows), not a correctness requirement.
