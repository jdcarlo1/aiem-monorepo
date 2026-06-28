---
name: Weekend blank tabs
description: Two root causes for tabs showing empty on weekends — and the fix pattern
---

# Rule
Every endpoint that does a live scan (background thread) needs TWO things to work on weekends:
1. **`_save_scan_cache(endpoint_name, out)`** called inside the background scan function when results are non-empty — so the data survives to the next restart.
2. **`if not _intraday_scan_allowed():`** check BEFORE launching the background thread — so on weekends it loads from DB instead of launching a scan that will silently fail.

**Why:** In-memory caches (`app._xyz_cache`) clear on every server restart. The startup preload restores most of them from DB within ~10s of boot, but only if data was previously saved. Without `_save_scan_cache()`, the DB is always empty for that endpoint. Without the weekend check, the server spins up a background scan, it silently fails (Tradier/Yahoo have no intraday data on weekends), and returns `{"generating": True}` forever.

**How to apply:** When adding any new tab endpoint with a background scan pattern, always add both. Pattern to verify:
- Look for `_save_scan_cache` inside the `_bg_*()` function
- Look for `if not _intraday_scan_allowed():` with `_load_scan_cache()` fallback BEFORE the thread launch

**Endpoints fixed:** composite-score (was returning {"generating": True} on weekends), sector-rotation (was never saving to DB).

**Key difference from market-hours hang:** Weekend blank ≠ market-hours spinner. Market-hours spinner = Yahoo throttle → endpoint hangs 18s+. Weekend blank = scan never ran/saved → DB empty → cache miss.
