---
name: Polygon vs Yahoo Finance migration plan
description: What Polygon Starter ($29/mo) can replace from Yahoo, what it can't, and the decision to wait before migrating
---

## What Polygon Starter CAN replace (verified with live API key)
- Historical OHLCV (`yf.history()`) → `/v2/aggs/ticker/{t}/range/1/day/{from}/{to}` ✅ works (DELAYED status is fine for swing trades)
- Previous day close (`fast_info.last_price`) → `/v2/aggs/ticker/{t}/prev` ✅ works (OK status)
- Float + market cap → `/v3/reference/tickers/{t}` returns `share_class_shares_outstanding`, `market_cap` ✅ works

## What Polygon Starter CANNOT replace
- Real-time intraday snapshot → `/v2/snapshot/locale/us/markets/stocks/tickers/{t}` returns NOT_AUTHORIZED on Starter plan

## Current Yahoo dependencies after 80% load reduction
- L4 Short Interest: `info.get("shortPercentOfFloat")` — still Yahoo; replaceable via free FINRA bi-weekly CSV import
- L6 Float: `info.get("share_class_shares_outstanding")` — still Yahoo; replaceable via Polygon reference (verified working)
- Real-time prices for 3 live-scan tabs (Market Overview, Morning Runners, Squeeze Setup) — still Yahoo; needs Starter→Business upgrade or Tradier $10/mo
- Options chains for IV Rank, Vol Crush, Max Pain tabs — still Yahoo `option_chain()`
- Earnings dates — still Yahoo

## Decision made
Do NOT migrate tonight before market open. Wait and see if reduced load (80% less than before) is sufficient. If tomorrow runs clean, migration may not be needed at all. If specific tabs still hang, target those specifically.

**Why:** Risk of breaking core conviction scoring (L4/L6) or outcome grading hours before market open outweighs the benefit. Polygon migration is additive improvement, not a critical fix.

## If migration is requested later
1. Switch `_get_short_interest()` → Polygon reference `/v3/reference/tickers/{t}` for float, FINRA CSV for short_pct
2. Switch `_update_conviction_outcomes()` yf.history() → Polygon aggregates
3. Switch scattered `fast_info.last_price` lookups → Polygon prev endpoint
4. Leave real-time tabs on Yahoo (or add Tradier $10/mo)
- Total Polygon-covered: ~90% of Yahoo calls; remaining 10% is real-time + options chains for analysis tabs
