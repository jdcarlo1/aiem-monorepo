---
name: Daily Tiered Movers system
description: Architecture, bugs fixed, and DB schema for daily_market_movers.py — the 17:10 ET per-tier winner/loser capture pipeline.
---

## Rule
`daily_market_movers.py` captures top-100 winners + bottom-100 losers per market-cap tier (Nano/Small/Mid/Large) daily at 17:10 ET, with full DB-resident feature enrichment.

## Polygon market cap source
**Only `/v3/reference/tickers/{ticker}` (single-ticker) returns `market_cap`.**  
The batch list `/v3/reference/tickers` returns `market_cap=None` for all tickers at standard subscription tiers. Use `fetch_market_caps_for_tickers(tickers, api_key, num_workers=20)` which threads 20 concurrent single-ticker calls.

**Why:** Confirmed via live API test (AAPL returned $4.63T from single endpoint, None from batch list).

## Cache NULL = uncached
`_get_market_cap_tiers_raw_set(conn)` returns ONLY tickers with `market_cap IS NOT NULL`.  
Tickers in cache with NULL market_cap (from old broken batch runs) are treated as uncached and re-fetched.

**How to apply:** If adding a cache check anywhere, always filter `WHERE market_cap IS NOT NULL`.

## Live-fetch ordering
In `run_daily_tiered_movers_job`, uncached tickers are sorted by `abs(pct_change)` descending before the 800-ticker cap. This ensures the top movers (most likely to be selected) get their market caps fetched first.

## DELETE-before-insert
Step 8 in `run_daily_tiered_movers_job` DELETEs all existing rows for `scan_date` before inserting the current run's results. This makes re-runs idempotent and prevents stale double-counted rows from prior broken passes from accumulating.

**Why:** Discovered that UPSERT-only leaves orphan rows when the selection changes between runs (e.g., after the disjoint winner/loser fix was applied).

## close_price >= 2.0 LAG filter
`_compute_daily_returns` filters `close_price >= 2.0` inside the CTE before the LAG window. This means for a ticker that dips below $2 for several days, the `prev_close_lag` for the first bar back above $2 will be the last bar that was >= $2 (potentially several days prior). The `pct_change` in daily_market_movers therefore represents a multi-day return in those cases. This is intentional (avoids comparing against penny-stock bars).

## Winner/loser disjoint fix
`_build_tier_movers` must exclude winner tickers from the loser candidate pool. When `tier_size < 2×top_n` (common for large-cap tiers), the bottom_n and top_n overlap and the same ticker gets both winner and loser rows. Fixed by building `winner_set` first and filtering it out before selecting losers.

## Non-existent columns in indicator tables
When writing enrichment SQL against these tables, the following expected columns DO NOT EXIST:
- `ai_short_calls_log`: no `gamma_score`, `dark_pool_score`, `squeeze_score`, `sector_heat_score` (actual: `vol_oi`, `prem`, `otm_pct`, `conviction`, `urgency`)
- `stat_arb_pairs`: no `current_zscore` (actual: `coint_pvalue`, `hedge_ratio`, `spread_mean`, `spread_std`)
- `aiem_predictions`: table does not exist at all (0 rows in information_schema.columns)

## Tier breakpoints
Nano: market_cap < $300M  
Small: $300M–$2B  
Mid: $2B–$10B  
Large: $10B+

## Scheduler registration (main.py)
- 17:10 ET Mon-Fri: `_daily_tiered_movers_job` (calls `run_daily_tiered_movers_job(top_n=100, api_key=POLYGON_API_KEY)`)
- 23:00 ET nightly: `_nightly_market_cap_cache_job` (calls `refresh_market_cap_cache` over full polygon_market_daily universe ~7K tickers, ~2–4 min with 20 threads)

## DB tables
- `ticker_market_cap_cache(ticker PK, market_cap BIGINT, fetched_at TIMESTAMPTZ)`
- `daily_market_movers(scan_date, ticker, direction UNIQUE constraint, pct_change, close_price, rank, market_cap_tier, feature_snapshot JSONB)`
