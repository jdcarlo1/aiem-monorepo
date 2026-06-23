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

## The durable fix (paid) — CHOSEN: Polygon / Massive.com (June 2026)
The real ceiling is the **data source**, not the code. A real-time market-data API
with a **bulk snapshot** endpoint removes the per-ticker polling entirely.

**User subscribed to Massive.com (formerly Polygon.io, rebranded Oct 30 2025) Starter plan ($29/mo)**
on June 22 2026. POLYGON_API_KEY is stored in Replit Secrets.

### Integration implemented (June 22 2026)
- `_polygon_fetch_calls(ticker, max_exp_days)` — one REST call to
  `/v3/snapshot/options/{ticker}?contract_type=call` returns all contracts across all expirations.
  Replaces N Yahoo `tk.option_chain(exp).calls` calls (one per expiry) per ticker.
- Stock price: `underlying_asset.price` is None on Starter plan → fetch via
  `/v2/aggs/ticker/{ticker}/prev` (previous-day close). This endpoint IS available on Starter.
- Yahoo is fallback-only (when POLYGON_API_KEY missing or Polygon errors).
- Both `_run_unusual_calls_scan` and the admin endpoint's custom-tickers `_scan_one` use Polygon.
- Market hours guard bypassed for `label in ("manual-trigger", "admin-eod")` so admin scans work after close.
- Verified working: 241 hits in a single after-hours scan (TSLA $407.5 at 64.5x vol/OI = $8.9M, SPCX 57.4x = $9.7M).

### Starter plan capabilities (confirmed June 22 2026)
- `/v3/snapshot/options/{ticker}` — ✅ works, 15-min delayed data
- `/v2/aggs/ticker/{ticker}/prev` — ✅ works, previous-day OHLCV
- `/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` — ❌ 403 NOT_AUTHORIZED (requires Developer+)
- Rate limits: REST API, no IP bans (quota-based), no throttle/block behavior like Yahoo

### Why this eliminates the Yahoo IP-block problem
Yahoo blocks the server's IP when option chain fetching volume is too high (10-20 requests
per ticker × 500+ tickers/scan = 5,000-10,000 requests → IP ban for hours).
Polygon counts against an API key quota but **never bans your IP**. Scanning every
15 minutes all day will never produce a blackout.

### Hard domain truth — OI is once-a-day (don't let anyone "fix" this)
Open interest is published by the OCC **after end-of-day clearing**, available next
pre-market. **No live/intraday OI exists anywhere** — intraday you only see *volume*.
The app already handles this right: 4:30 PM EOD OI snapshot + 8:30 AM pre-market
refresh, then **Vol vs OI** is the "new positions opening" signal.

**Why:** user repeatedly hit missed-mover incidents caused by yfinance throttling
and blind spots; on June 22 2026 Yahoo blocked the production IP all afternoon.
Polygon/Massive.com solves this permanently.

## Intraday unusual-calls scan: market-hours fast path (June 23 2026)

During 9:30-16:00 ET, Polygon Starter returns `open_interest=0` for ALL option contracts
(OI is published by OCC after EOD clearing — no intraday OI exists anywhere).

Before this fix, `_scan_one` received `pg_rows` from Polygon (OI=0 on all contracts), so
`_pg_has_oi = False`, and ALL 1238 tickers fell through to the Yahoo fallback. Each ticker
needed ~6 Yahoo API calls (1 fast_info + 1 options list + 4 chains). At 3/sec global rate
limit: 1238 × 6 = 7428 calls / 3 = 2476 seconds. The 180-second timeout means ~90
tickers get scanned → all from the leaderboard tail, not priority names → 0 qualifying
sweeps → HC tab shows stale data all day.

**Fix:** At the top of `_scan_one`, check if we're in market hours:
```python
_mkt_hours = (9, 30) <= (_et_now.hour, _et_now.minute) < (16, 0)
if _mkt_hours and not is_etf and ticker not in set(_PRIORITY_FIRST):
    return hits  # skip — EOD Polygon scan covers this ticker at 16:00+
```
This limits intraday Yahoo calls to ~95 tickers (45 priority + ~50 ETFs).
At 3/sec: 95 × 6 = 570 calls / 3 = 190 seconds — barely fits the 180s timeout
but priority names run first via ThreadPoolExecutor.

Also added: `if _yf_breaker_open(): return hits` before the Yahoo rate-limiter acquire,
and capped expiry chains to 4 nearest valid (not all 20+) to reduce calls per ticker.

**Coverage contract:**
- 9:30-16:00: priority large-caps + ETFs scanned via Yahoo every ~60 min (vol/OI from Yahoo live data)
- 16:00+: full 1238+ ticker EOD scan via Polygon (OI now populated for vol/OI ratio)

## Rotating leaderboard cursor (June 22 2026)
`_lb_cursor` + `_lb_cursor_lock` globals advance by 1,000 tickers per hourly scan.
7 hourly scans × 1,000 tickers = full 6,610 universe covered by ~3:10 PM ET each trading day.
Universe per scan = `_PRIORITY_FIRST (21) + _earnings + _movers + lb[cursor:cursor+1000]` → ~1,100-1,200 total.
Cursor resets to 0 on restart (intentional — priority names always first after a restart).
Log format: `[scheduler] manual-trigger scan universe: 21 priority + 0 earnings + 277 movers + lb[0:1000] = 1184 total`

## Priority scan order fix (June 22 2026)
SPY/QQQ were at positions 463/464 in the DEFAULT_LEADERBOARD scan order — meaning if Yahoo
blocked at position 200, the highest-volume ETFs were always missed. Fixed by prepending a
`_PRIORITY_FIRST` list (TSLA, NVDA, AAPL, MSFT, AMZN, META, AMD, GOOGL, COIN, MSTR,
PLTR, ARM, HOOD, MU, MRVL, WDC, SMCI, INTC, AVGO, NFLX, UBER) to the scan universe
in `_run_unusual_calls_scan`. These names now always scan in the first batches regardless
of throttle timing.

## Production IP block (June 22 2026 — confirmed, now resolved by Polygon)
Yahoo blocked the production server's IP for the ENTIRE AFTERNOON. Every ticker
in every scan returned "circuit breaker open (Yahoo rate-limited)". Resetting the
breaker + re-triggering manually did NOT help. SMCI was the only name visible on
the High Conviction tab all afternoon. **Polygon eliminates this problem.**

**How to apply:** when asked to widen coverage (more tickers/cap tiers), don't grow
the polling universe — either (a) use screener calls for live breadth, or (b) extend
the Polygon path. Reserve per-ticker `history()` for a bounded, high-conviction watchlist.
