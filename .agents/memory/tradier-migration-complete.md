---
name: Tradier + Polygon migration — full audit (two-pass)
description: What moved off Yahoo Finance, what stays, and why — definitive reference
---

## Pass 1 — OHLCV / intraday / quotes (31 replacements)
All .history() and fast_info.last_price calls → _td_history / _td_intraday / _td_quotes.
market_overview went from 216 Yahoo calls → 2 Tradier batch calls.
See original migration notes for the full list.

## Pass 2 — market_cap / fundamentals (4 replacements)
New helper: `_pg_market_cap_batch(tickers)` at ~L597 in main.py.
  - Polygon v3/reference/tickers/{ticker} per ticker, threaded 8-way
  - 4-hour in-memory cache (_pg_cap_cache dict)
  - Uses urllib.request (not requests) — bypasses the yfinance curl_cffi patch

Replaced:
  - _smp_market_caps() Yahoo fast_info fallback → _pg_market_cap_batch()
  - _compute_flow_mc() microcap net-flow → _pg_market_cap_batch([ticker])
  - _compute() multiday net-flow → _pg_market_cap_batch([ticker])
  - morning_runners _scan_mr() — fully off Yahoo:
      last_price/prev_close/avg_vol/today_vol → _td_quotes() batch
      market_cap → _pg_market_cap_batch()
      Yahoo breaker guard removed (endpoint no longer touches Yahoo)

## Yahoo Finance — KEEP (confirmed unreplaceable)
- ^VIX / ^VIX3M — no free real-time index feed; FRED is EOD only
- float_shares — Tradier ownership_summary.float returns 0 for large caps;
  Polygon has share_class_shares_outstanding (total, not float)
- short_interest / shortPercentOfFloat / shortRatio — neither Tradier nor Polygon
- earnings dates / tk.calendar — Tradier fundamentals/calendars is incomplete
  (shows only confirmed past dates + very long-range estimates, misses next quarter)
- year_high / year_low — Tradier quotes don't expose 52-week range; would require
  fetching 252 days of history and computing max/min (extra call per ticker)
- tk.options / tk.option_chain() — used in gamma pressure, smart money, breakout
  (Tradier option chains used separately in _tradier_fetch_calls() for unusual-calls)

**Why these stayed on Yahoo:** tested each against Tradier and Polygon APIs directly;
Tradier fundamentals data quality confirmed unreliable for earnings; float = 0 for AAPL.

**How to apply:** Never add new `.history()` or `fast_info` calls for price/OHLCV.
Use _td_history / _td_intraday / _td_quotes. For market_cap use _pg_market_cap_batch.
Keep Yahoo only for the 5 categories above.

## Smoke test results (post both passes)
20/20 endpoints PASS, 0 FAIL. All cached tabs <500ms.
