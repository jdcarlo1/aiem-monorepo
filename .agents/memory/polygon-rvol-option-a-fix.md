---
name: polygon_rvol scan Option A fix
description: Root cause of July 14/17/23 0-row failures + fix deployed 2026-07-27
---

## Rule
`_polygon_full_market_scan()` now makes exactly 1 Polygon API call (was 5). Prior-day volumes come from `polygon_market_daily` DB.

**Why:**
The old code called `_polygon_grouped_daily()` 5 times with 13s sleeps (52s total). All 5 calls within a 60-second window hit Polygon Starter's 5 req/min quota. Calls 2-5 returned `{}` → `prior_days=[]` → `len(_pvols)<2` gate eliminated every ticker → 0 rows in `polygon_rvol_scan`. Affected dates: July 14, July 17, July 23.

**Diagnostic fingerprint:**
`polygon_market_daily.rvol = NULL` for ALL rows on a given scan_date = `prior_days=[]` during that scan. Volume IS always written even when rvol=NULL — so prior-day volumes are available in DB for subsequent scans. This is the exact DB signal to look for on future failures.

**How to apply:**
- If `polygon_rvol_scan` has 0 rows for a date, check `polygon_market_daily.rvol` for that date.
- ALL NULL rvol + full volume data = prior-day API calls failed during scan (old bug, now fixed).
- ALL NULL rvol + missing volume data = the Polygon grouped-daily call itself failed (new concern).
- The scan now reads prior-day volumes via `SELECT ticker, scan_date, volume, close_price FROM polygon_market_daily WHERE scan_date = ANY(%s::date[])`.

**Timing dependency (confirmed safe):**
Prior days' rows are written by previous scan runs (≥24h ago). No race condition. 4/4 prior days had 7,000+ tickers with volume>0 at time of fix deployment.

**Negative control behavior:**
If DB query fails → `_prior_vol_map={}` → all tickers hit `len(_pvols)<2` → 0 movers. Logged as `app.logger.error()` (not silent). Same observable outcome as old bug but with explicit error in crash_log_buffer.

**Evidence chain:**
- Before sha256: `47d438aed8bff5dfce8fbb574d2df75950a7b3b6ad5f0417cecd0ab99864685b`
- After sha256:  `d5a415628abda1ae01b25c38763490e7ce113287cd5271ea76e0c5d2864c8d5a`
- verified_run SEQ=108, entry_hash=`1fb60c007431cad9...` PSV 8/9 PASS
- Live proof: 1 API call, 37 movers for 2026-07-24, rvol_nulls=0
