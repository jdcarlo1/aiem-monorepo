---
name: Scanner data-source ceiling
description: Why the yfinance polling scanner can't cover the full market, and the architectural fix (full-market snapshot API). Read before "just add more tickers to the scan."
---

# Scanner data-source ceiling

## The constraint
`morning_inflows()` fetches data **per-ticker** (`yf.Ticker(t).history(period="1d", interval="1m")`)
across a ThreadPoolExecutor (max_workers≈25). This polling model caps out around
**~1,200 tickers per scan cycle** before Yahoo throttles (`YFRateLimitError`). The
scan runs every 2–3 minutes in the open, so each cycle must finish fast.

## Why you can't just "add micro + small caps"
Finviz universe sizes (price>$0.50, avgvol>20K), as of June 2026:
- nano (`cap_nano`): ~948
- micro (`cap_micro`): ~1,206
- small (`cap_small`): ~1,652
- combined nano+micro+small ≈ **3,800 tickers**

Dumping all of these into the per-ticker morning poll would be ~5,000 tickers/cycle —
it will rate-limit and blow the cycle budget. **Do not feed the full micro/small
universe into `morning_inflows`.**

## The right architecture (two scalable patterns)
1. **EOD batch scan** (once daily, not time-critical): can scan all 3,800 with
   ThreadPoolExecutor over a few minutes. Fine for the breakout/setup watchlist.
2. **Live coverage at scale**: use **screener calls** (finviz cap_* + ta_change,
   Yahoo day_gainers) — a handful of HTTP requests return the movers across an
   entire tier — instead of polling thousands of tickers individually.

## The durable fix (paid)
The real ceiling is the **data source**, not the code. A real-time market-data API
with a **full-market snapshot** endpoint (one call returns price/%chg/volume for
every US stock) removes the per-ticker polling entirely:
- **Polygon.io** (most accessible for a retail scanner; ~$29/mo Starter for
  real-time snapshots + websocket), Databento, IQFeed/DTN, Alpaca.
- The "snapshot all tickers" call covers every cap tier at once → micro/small
  coverage becomes trivial and rate-limit failures disappear.

**Why:** user repeatedly hit missed-mover incidents caused by yfinance throttling
and blind spots; on June 16 2026 we established the polling ceiling and that the
fix is a snapshot feed, not more tickers in the loop.

**How to apply:** when asked to widen coverage (more tickers/cap tiers), don't grow
the polling universe — either (a) use screener calls for live breadth, or (b)
propose the Polygon snapshot path. Reserve per-ticker `history()` for a bounded,
high-conviction watchlist.
