---
name: Tradier migration complete — Yahoo Finance audit
description: Full list of what stays on Yahoo vs what moved to Tradier, and the migration rules
---

## Yahoo Finance — KEEP (no Tradier equivalent)
- `fast_info.market_cap` — used in microcap/multiday flow scans
- `fast_info.float_shares` / `.shares` — used in gamma pressure, float scoring
- `tk.info` dict — short_interest, shortPercentOfFloat, shortRatio, sector, industry
- `tk.calendar` — earnings dates
- `yf.screen` — equity screener (Polygon has no screener API)
- `^VIX`, `^VIX3M` — Tradier has no index price feed for CBOE volatility indices
- `tk.options` + `tk.option_chain()` — Tradier handles this separately via `_tradier_fetch_calls()` for the unusual-calls scanner; breakout/composite/vol-crush still use Yahoo option chains because tkr object is reused for multiple attributes in the same function

## Tradier now handles (31 replacements made)
- All `tk.history(interval="1d")` → `_td_history(ticker, days=N)` or `_td_history(ticker, start_date="YYYY-MM-DD")`
- All `tk.history(interval="1m")` → `_td_intraday(ticker, "1min")`
- All `tk.history(interval="5m")` → `_td_intraday(ticker, "5min")`
- `fast_info.last_price` in AI trades live price refresh → `_td_quotes(batch)[t]["last"]`
- `market_overview` sector/index/A/D prices → `_td_quotes()` batch calls

## market_overview improvement
Before: 216 Yahoo `.history(period="5d")` calls (11 sectors + 5 indices + 200 A/D)
After: 2 Tradier batch calls (1 for sectors+indices, 1 for A/D) + 1 Yahoo call (^VIX only)

## Smoke test result (post-migration)
26/26 endpoints PASS, 0 FAIL. Cached tabs <500ms. Live Tradier fetches <6s.

**Why:** Yahoo rate limits / 429s during market-open burst caused all-day blank tabs.
**How to apply:** Never add new `.history()` or `fast_info.last_price` calls — use `_td_history`/`_td_intraday`/`_td_quotes` instead. Yahoo is only for the 5 categories above.
