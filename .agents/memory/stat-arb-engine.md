---
name: Statistical Arbitrage Engine
description: stat_arb_engine.py — cointegration + z-score spread engine, full AIEM integration details
---

# Statistical Arbitrage Engine

## The Rule
`stat_arb_engine.py` lives at `artifacts/stock-scanner-api/stat_arb_engine.py`. It is a standalone module imported lazily (`import stat_arb_engine as _sae`) everywhere.

**Why:** lazy import keeps Flask startup fast; the module is only loaded on first call.

## How to Apply
Adding a new pair: add a `(ticker_a, ticker_b)` tuple to `DEFAULT_PAIRS` in stat_arb_engine.py. Pairs must be tested first via `stat_arb_daily_scan(retest_pairs=True)` before they produce z-score signals.

## Critical psycopg2 Bug (already fixed)
`INTERVAL '%s days'` does NOT work as a parameterized query — psycopg2 cannot substitute inside a string literal.
Fix: use `(%s * INTERVAL '1 day')` instead. This is already applied in both `_fetch_closes` and `get_recent_signals`.

## Integration Points (all wired)
1. `_aiem_tool_stat_arb_check_wrapper` — wrapper in main.py before `_build_aiem_tool_map`
2. `"stat_arb_check"` in `_build_aiem_tool_map()` dispatch dict
3. Schema entry in `_AIEM_AGENT_TOOLS` list
4. `_DEFERRED_INITS.append(lambda: __import__("stat_arb_engine")._init_tables())`
5. Scheduler: daily 9:10 AM ET (`stat_arb_daily_scan`) + Sunday 3 PM ET (`stat_arb_daily_scan(retest_pairs=True)`)
6. Flask endpoint: `GET /stock-api/stat-arb/signals?days=5`

## Tables
- `stat_arb_pairs` — cointegration registry (ticker_a, ticker_b, pvalue, hedge_ratio, spread_mean, spread_std)
- `stat_arb_signals` — daily z-score log (signal_date, zscore, direction, signal_strength)

## Signal Flow
Sunday 3 PM → cointegration retest → register pairs in DB → Mon-Fri 9:10 AM → z-score scan → log signals → AIEM tool checks ticker on demand.

## First-run Bootstrap
On first Monday after deploy, no pairs are in DB yet (retest hasn't run). Run manually if needed:
```
import stat_arb_engine as sae; sae.stat_arb_daily_scan(retest_pairs=True)
```
Or trigger via AIEM: "run stat_arb_check on NVDA" — it returns conviction_boost: NONE with pairs_found: 0 until retest runs.
