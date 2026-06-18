---
name: Paid market-data feed options (Alpaca vs Polygon) for the conviction score
description: Coverage/cost facts for replacing the throttled free yfinance feed — which paid feed supplies the OI + options + short-interest + dark-pool the L1-L8 score needs
---

# Paid data feed options for StockScanner AI

**Why this matters:** the L1-L8 conviction score (and IV Rank, Squeeze Radar, etc.) all pull option chains + open interest from the FREE Yahoo feed via yfinance, which Yahoo rate-limits hard ("Too Many Requests"). The score's per-ticker option-chain crawl is the bottleneck — caps the morning scored cohort at ~60 (CONVICTION_STACK_MAX) and makes single lookups take ~24s. The durable fix is a paid feed. This file is the coverage map so a future agent doesn't re-research it.

## What the score actually needs
- Option chains: volume, **open interest (OI)**, implied volatility — per expiration (far-OTM sweep, OI accumulation, gamma layers).
- **Short interest %** / days-to-cover (L4 Short Interest layer).
- **Dark-pool volume** (L4 Dark Pool layer).

## Alpaca ($99/mo "Algo Trader Plus" for real-time; free tier is IEX-only/15-min-delayed = unusable here)
- Stocks: full SIP real-time, 10,000 req/min (vs Yahoo throttling). Fixes the throttling.
- Options: real-time quotes/trades + **IV + Greeks** via `/v1beta1/options/snapshots` and `/chain`.
- **Open interest: YES, but only via the `/v2/options/contracts` endpoint, and it's END-OF-DAY (prior close), NOT in the snapshot/chain endpoints.**
- **Short interest: NO.** Only a `shortable`/`easy_to_borrow` boolean on the assets endpoint — no SI%, float, days-to-cover.
- **Dark pool: NO** dedicated feed (SIP includes dark-pool prints indirectly, not a usable dark-pool % signal).
- => Alpaca alone would knock out the Short Interest AND Dark Pool layers of the score.

## Polygon.io (leaning choice for this app)
- **Full-market snapshot in ONE call** (`/v2/snapshot/locale/us/markets/stocks/tickers`) — fixes BOTH throttling AND the ~60/~1,200 ticker ceiling (can score hundreds/thousands).
- Options chain snapshots include OI + IV + Greeks.
- **Has dark-pool data** (Alpaca does not) — covers more of the score's layers.
- Short interest still needs a separate source (FINRA) on either provider.

## Key truth to set expectations
**OI is end-of-day EVERYWHERE** — OCC publishes it once daily. Yahoo's OI is also end-of-day. So no paid feed makes OI "real-time"; the paid win is reliability/speed/coverage, not OI freshness.

**Bottom line:** Polygon covers more of this specific score (dark pool + full-market snapshot) than Alpaca. Short interest needs a 3rd source regardless. Any switch is a real migration (whole app is wired to yfinance), not a config flip.
