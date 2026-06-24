---
name: Polygon full-market RVOL scanner
description: Daily 8:35 AM scan of all 11,000+ US stocks for unusual relative volume using Polygon grouped daily endpoint. Zero Yahoo dependency.
---

## What was built
- `_polygon_recent_trading_days(n)` — returns n+3 candidate days (ET, weekdays only) so holidays can be skipped
- `_polygon_grouped_daily(date_str)` — fetches all US stocks OHLCV for one date; skips days with 0 results (holidays)
- `_polygon_full_market_scan()` — main scan; 5 API calls for 5 valid trading days, filters for RVOL≥5x + gap≥3% + price $1-$50 + vol≥150K + bullish close; stores in `app._polygon_rvol_cache` and DB
- `_get_polygon_rvol_data()` — DB fallback for cold restarts
- `_send_polygon_rvol_email()` — 8:35 AM owner email with top 25 movers, close-strength bar chart
- `/stock-api/full-market-movers` GET — returns cached scan
- `/stock-api/admin/run-polygon-rvol` POST — admin trigger (X-Admin-Token header)
- `"polygon_rvol": [(8, 35)]` added to `_OWNER_EMAIL_SCHEDULE`

## Critical: use urllib.request, NOT requests
The global `requests.Session.__init__` is patched in main.py to inject the Yahoo circuit-breaker adapter. ALL `requests.get()` calls (including Polygon) go through this patch. Using `urllib.request.urlopen()` directly bypasses it entirely. Never replace with `requests.get()` or it silently returns 0 movers.

## DB table
`polygon_rvol_scan` — columns: scan_date, ticker, price, open_price, high, low, vwap, gap_pct, volume, avg_volume, rvol, close_strength. UNIQUE(scan_date, ticker).

## Rate limit: 13s sleep + lock (CRITICAL)
Polygon Starter = 5 req/min. Must sleep 13s between each grouped-daily call or Polygon returns 429.
`_POLYGON_RVOL_LOCK` (threading.Lock) prevents concurrent runs (startup catchup + admin trigger firing simultaneously) from doubling the call rate. Non-blocking acquire: second caller returns cached data immediately.
June 19 (Juneteenth) = 0 results from Polygon — expected, function skips and tries next day.

## What the scan catches
- RTB appeared in June 22 scan at #26 (RVOL=5.1x, +20.4%) — the stock that ran +151% over 5 days
- ALOY did NOT appear on June 10 (catalyst day) because it had already been running — avg volume was 3M/day so 2.9M on June 10 = 0.9x RVOL. Initial breakout from $10 base (late May) is when it would have appeared.

## Key insight: RVOL only catches Day 1
Once a stock has been running for days/weeks, its "normal" volume resets higher. The scan catches the initial catalyst breakout. To capture the full multi-week move, need a follow-on "continuation watchlist" (next feature to build) that tracks stocks that appeared in prior scans and checks if they're still holding structure.

## Close strength metric
`close_strength = (close - low) / (high - low)`. 100% = closed at HOD = institutional accumulation. Best continuation candidates are RVOL≥10x AND close_strength≥80%. These are the BHVN/ALOY setups.
