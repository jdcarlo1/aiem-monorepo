---
name: td_intraday_cache implementation
description: Schema, scheduler job, and guards for the 1-min intraday bar capture table (AIEM hypothesis #12 prereq)
---

## Schema
```sql
td_intraday_cache (
    id SERIAL PK, ticker VARCHAR(10), ts TIMESTAMPTZ,
    open FLOAT, high FLOAT, low FLOAT, close FLOAT, volume BIGINT, vwap FLOAT,
    UNIQUE(ticker, ts)
)
INDEX td_intraday_ticker_ts ON (ticker, ts DESC)
```

## Data source
`_td_intraday(ticker, "1min")` → Tradier `/v1/markets/timesales` with `session_filter="open"`.
Returns full session bars per call (not just the latest bar).
On a closed market day: HTTP 200 `{"series":null}` → empty DataFrame → 0 DB writes.

## Capture logic
- `_TD_INTRADAY_WATCHLIST`: 50 hardcoded priority tickers (semis + megacaps + ETFs)
- Extended at runtime: up to 20 names from `_unusual_calls_cache["hits"]` + 10 from `_cs_cache["results"]`
- `_save_td_intraday_bars()`: `execute_values` with `ON CONFLICT (ticker, ts) DO NOTHING` — idempotent
- `_run_td_intraday_capture()`: `ThreadPoolExecutor(max_workers=4, timeout=90s)`

## Scheduler job: td_intraday_capture
- Trigger: `interval`, every 5 min
- Gate 1 (wrapper): `_intraday_scan_allowed()` — handles weekends + 2026 NYSE holidays + 9:30-4:30 ET
- Gate 2 (wrapper): narrow window 9:35 AM–16:00 ET (market settle time + hard cutoff)
- Gate 3 (capture fn): redundant `_intraday_scan_allowed()` call as belt-and-suspenders
- Runs as daemon thread

## Holiday calendar
`_intraday_scan_allowed()` uses a **hardcoded 2026-only frozenset** (10 dates).
No library (no pandas_market_calendars). Startup warning fires if `date.today().year > 2026`.

**Why:** Simple and zero-dependency, but must be updated manually each year.

## Accumulation estimate for hypothesis #12
- 50 tickers × 390 bars/day = ~19,500 rows/day
- min_n=50 instances for signal testing: ~3–5 trading days
- min_n=200 for robust backtest: ~10–15 trading days
