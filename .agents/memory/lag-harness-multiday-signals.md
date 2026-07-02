---
name: LAG-aware Fisher harness for multi-day pattern signals
description: run_fisher_test_lag() in aiem_stat_tests.py — when to use it vs standard harness, column names, and first validated result
---

`aiem_stat_tests.run_fisher_test_lag()` extends the standard non-overlapping bucketed Fisher test by computing LAG(1) columns in the CTE before applying `sql_filter`.

**LAG columns available (reference WITHOUT 'pm.' prefix in sql_filter):**
- `prev_close_strength` — yesterday's close_strength (0–1)
- `prev_move_pct` — ABS(yesterday close − prev_close) / prev_close × 100
- `prev_rvol` — yesterday's rvol
- `prev_gap_pct` — yesterday's gap_pct
- `prev_range_pct` — yesterday's range_pct

Single-day columns are also available without prefix: `close_price`, `prev_close`, `rvol`, `close_strength`, `gap_pct`, `volume`, `range_pct`.

**When to use:** Any signal where the entry condition depends on YESTERDAY's price action (e.g., big catalyst day → inside day → gap-up). Use `run_fisher_test` (standard) for single-day conditions that reference only today's row via `pm.` prefix.

**Why:** Standard harness embeds the filter inside the LEAD/ROW_NUMBER CTE, so LAG columns can't be computed there without a second CTE layer. The LAG variant uses a `raw` CTE with all window functions, then an `annotated` CTE that applies `sql_filter` — two-pass approach.

**First validated result (2026-07-02):**
- id=3 (inside-day-after-3%-move, loose threshold): n=261, WR=61.69%, base=53.26%, delta=8.43pp, p=0.0037 → ✅ SURVIVES
- id=2 (inside-day-after-5%-move, tight threshold): n=17, WR=76.47%, delta=23.2pp, p=0.045 → ❌ n<50 (suggestive but insufficient given polygon_market_daily data since 2024-07-08)

**Note on retestability tracker:** `aiem_discovery_outcomes.retestable=False` with `skip_code='no_forward_data_yet'` was the old answer for multi-day/lag-delta schemas. These are now retestable with `run_fisher_test_lag()`. When adding a new multi-day signal via Module 5/6, use LAG harness directly — don't mark retestable=False unless the LAG columns needed aren't in polygon_market_daily.
