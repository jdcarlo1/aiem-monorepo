---
name: Market-open tab spinners root causes
description: Why market_overview and squeeze_setup spun forever; circuit-breaker gaps across 5 endpoints
---

## The _yahoo_breaker NameError bug
`_yahoo_breaker.allow()` / `.record_success()` / `.record_failure()` were called in
the background threads of `market_overview` (line ~12461) and `squeeze_setup` (~19309)
but the object was **never defined** anywhere in main.py. The background threads crashed
with a silent `NameError`, the scans never completed, and the tabs showed the spinner
indefinitely.

**Fix:** Added `_YFBreakerCompat` class right after `_yf_breaker_open()` that routes
all three methods through the existing `_YF_BREAKER` / `_yf_breaker_trip()` / 
`_yf_breaker_probe_success()` machinery. Defined as `_yahoo_breaker = _YFBreakerCompat()`.

**Why:** The two endpoints were written with a slightly different breaker API before the
main `_YF_BREAKER` dict pattern was standardized. The class was never created.

## 5 endpoints missing breaker guard
`convergence`, `composite-score`, `52week-breakout`, `earnings-calendar`, `multi-signal`
all used the correct background-thread pattern (return immediately, scan in daemon thread)
but launched background scans even when `_yf_breaker_open()` was True — burning Yahoo
quota during throttle windows. Each got a `_yf_breaker_open()` early-exit added.

**Pattern:** Place the breaker check BEFORE `threading.Thread(target=_bg_*).start()`,
not inside the background function, so the scan is never launched when throttled.

## Breaker conventions across the file
- `_yf_breaker_open()` → True when breaker is NOT closed (use this for endpoint guards)
- `_yf_breaker_trip()` → force-open the breaker (on 429/401 burst detection)  
- `_yf_breaker_probe_success()` → close the breaker (on successful half-open probe)
- `_yahoo_breaker` → compat shim, same semantics, for the two legacy call sites
