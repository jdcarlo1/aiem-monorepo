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

## The durable fix (paid) — CHOSEN: Alpaca (June 2026)
The real ceiling is the **data source**, not the code. A real-time market-data API
with a **full-market snapshot** endpoint (one call returns price/%chg/volume for
every US stock) removes the per-ticker polling entirely.
- Candidates were Polygon.io, Databento, IQFeed/DTN, Alpaca. **User chose Alpaca.**
- Alpaca needs the **paid data tier** ("Algo Trader Plus", ~$99/mo) for the full
  US SIP feed; the **free tier is IEX-only (~3% of volume)** and is NOT enough.
- No Replit integration exists for Alpaca → plain **API Key ID + Secret Key**
  stored via the environment-secrets skill (never in code).
- The "snapshot all tickers" call covers every cap tier at once → micro/small
  coverage becomes trivial and rate-limit failures disappear.

### Why Alpaca specifically (the conviction-score goal)
User's real driver is an **accurate smart-money-pressure / L1-L8 conviction score**
across the *whole* market, not just feed breadth. Alpaca uniquely adds, beyond a
snapshot feed:
- **Real OPRA/OCC open interest** on far more strikes/tickers than patchy Yahoo,
  with no rate-limit choke → better Vol/OI signal at scale.
- **A live options trade tape + bid/ask quotes** → enables true **aggressor-side**
  (at-ask = buyer/bullish, at-bid = seller) + **sweep** detection — the *intraday*
  half of smart-money pressure Yahoo snapshots can't give.
- **Greeks/IV** for the gamma- and float-pressure layers.

### Hard domain truth — OI is once-a-day (don't let anyone "fix" this)
Open interest is published by the OCC **after end-of-day clearing**, available next
pre-market. **No live/intraday OI exists anywhere** (not Alpaca, not anyone) —
intraday you only see *volume*. The app already handles this right: 4:30 PM EOD OI
snapshot + 8:30 AM pre-market refresh, then **Vol vs OI** is the "new positions
opening" signal. Alpaca upgrades OI *quality + breadth*, NOT *freshness*. Reject any
plan premised on "live OI."

**Why:** user repeatedly hit missed-mover incidents caused by yfinance throttling
and blind spots; on June 16 2026 we established the polling ceiling, and in June
2026 the user committed to Alpaca to power an accurate conviction score market-wide.

## Production IP block (June 22 2026 — confirmed)
Yahoo blocked the production server's IP for the ENTIRE AFTERNOON. Every ticker
in every scan returned "circuit breaker open (Yahoo rate-limited)". Resetting the
breaker + re-triggering manually did NOT help — Yahoo re-blocked within seconds of
each attempt. SMCI (captured at 12:41 PM ET before the block) was the only name
visible on the High Conviction tab all afternoon. **Do not tell the user manual
scans will produce results when the production IP is Yahoo-blocked — they won't.
The accumulation fix (June 22) ensures sweeps captured before the block persist all
day, but cannot recover names Yahoo prevented from being scanned in the first place.
The only real fix is Alpaca.**

**How to apply:** when asked to widen coverage (more tickers/cap tiers), don't grow
the polling universe — either (a) use screener calls for live breadth, or (b) build
the Alpaca path. Sequence after the Reserved-VM republish; it rewires the data layer
(18k-line main.py) so plan it (architect) before coding. Reserve per-ticker
`history()` for a bounded, high-conviction watchlist.
