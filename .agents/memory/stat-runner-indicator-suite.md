---
name: Stat runner full indicator suite
description: Architecture of aiem_stat_research_runner.py temp table and cell bins — what's wired, what's impossible, naming conventions
---

## Current state (July 2026)
- **232 cells × 2 outcomes = 464 Bonferroni-corrected tests**
- Temp table: `_hb_tmp`, ~2.2M rows from `polygon_market_daily`
- Filter: close_price 2–200, volume ≥100k, open_price > 0

## Named SQL windows in inner subquery
```sql
WINDOW w   AS (PARTITION BY ticker ORDER BY scan_date),           -- LAG only
       w14 AS (... ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING),   -- Williams %R
       w20 AS (... ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),   -- BB + SMA20 + S/R
       w50 AS (... ROWS BETWEEN 50 PRECEDING AND 1 PRECEDING)    -- SMA50
```

## CTEs
- `spy_ctx` — SPY gap_pct and close_strength per day
- `vix_ctx` — UVXY (VIX proxy) daily % change (UVXY in polygon_market_daily, 496 days)
- `mkt_breadth` — per-day fraction of stocks that closed up (advance/decline ratio)

## Outer SELECT derived columns (indicators)
| Column | Formula |
|--------|---------|
| `bb_z` | (open - sma20) / stddev20 — Bollinger Band z-score |
| `bb_squeeze` | stddev20 / sma20 < 0.025 — tight bands |
| `above_sma20` | open > sma20 |
| `above_sma50` | open > sma50 |
| `at_resistance` | open >= resist20 * 0.98 |
| `breakout_20d` | open > resist20 |
| `near_support` | open <= support20 * 1.02 |
| `williams_r` | (high14 - open) / (high14 - low14) * -100 — RSI proxy |
| `uvxy_chg_pct` | from vix_ctx join |
| `adv_ratio` | from mkt_breadth join |

## Cell bin groups
- gap_bins, rvol_bins, pcs_bins, px_bins, vol_bins, prior_bins, combos
- vwap_bins, high_ext_bins, trend_bins, trend_combos
- atr_bins, spy_bins, streak_bins, market_combos (pre-existing)
- **bb_bins** (10), **sma_bins** (10), **sr_bins** (8), **willr_bins** (8), **vix_bins** (8), **breadth_bins** (10) — added July 2026

## Impossible indicators (require tick/L2 data)
Cumulative Delta, Time & Sales, Level II, TICK, TRIN, order book imbalance, iceberg orders — not in polygon_market_daily

## Convention
- wr_signal and wr_baseline are stored as already-percentage values (e.g. 36.5, not 0.365)
- Cell keys prefixed `hb_`
- Bonferroni threshold = 0.05 / (2 × n_cells)
- Min n_signal = 30 before Fisher test runs

**Why:** AIEM queries aiem_historical_pattern_grid to find high-WR setups; needs to know what columns exist and what's computable vs impossible from daily data.
