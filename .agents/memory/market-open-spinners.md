---
name: Market-open tab spinners — root causes and fixes
description: Why tabs spin at 9:30 AM market open and the layered fixes applied
---

## Root causes (confirmed June 2026)
1. **Scheduler burst**: many jobs fire 9:30-9:45 when Reserved VM (always-on) actually
   runs all of them. APScheduler default config (max_workers=10, misfire_grace_time=1s,
   coalesce=False) lets jobs stack. Fixed to: ThreadPool(max_workers=4), coalesce=True,
   max_instances=1, misfire_grace_time=600.

2. **Inline live fetches**: endpoints that do synchronous yfinance inside the HTTP worker
   thread hang 8-18s when Yahoo throttles. Fixed by: bg-thread pattern (start scan, return
   stale cache immediately) + _yf_breaker_open() guard before any live fetch.

3. **No DB fallback on cold cache**: after a restart the in-memory cache is empty and the
   bg thread hasn't finished yet → blank tab. Fixed by loading _load_scan_cache("tab-id")
   before returning the empty generating:True response.

## Morning burst schedule (as of 2026-06-30)
Critical window: 9:36 AM ET (market_open_unusual_calls — heaviest daily yfinance scan)
- 9:35 AM: aiem_paper_execute (Tradier + DB only — safe)
- 9:36 AM: market_open_unusual_calls ← DO NOT add jobs near this slot
- 9:45-9:59/5: premarket_open_tracker
- 9:52 AM: morning_inflows_email (was 9:42 → moved to clear the scan window)
- 10:00 AM+: ai_trades_auto, top_pick_email, sms_alert_scan, ai_short_calls_auto, etc.

**Why:** 9:42 job competed for yfinance token bucket during the heaviest scan;
pushing to 9:52 gives 16 min of headroom.

## Breaker cooldown
_YF_BREAKER_COOLDOWN = 300s. Do not reduce. After any test that trips the breaker,
wait 5 min or call POST /stock-api/admin/reset-breaker (requires X-Admin-Token header).

## Endpoints safe without Yahoo breaker guard
- 52week-breakout: _td_quotes (Tradier) only
- morning-runners: _td_quotes + _pg_market_cap_batch (Tradier + Polygon) only
- daily-top10: memory → DB → bg refresh pattern, no live fetch in HTTP thread
- outcomes: pure DB query (_get_signal_outcomes)
