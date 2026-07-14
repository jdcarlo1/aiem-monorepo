---
name: mkt backfill blocking module load
description: _mkt_backfill_indicators_all() ran synchronously on the module loading thread for 4+ minutes; fix pattern and _MODULE_FULLY_LOADED gate
---

## Rule
`_mkt_backfill_indicators_all()` (and `_polygon_backfill_historical()`) compute/download
data for 12K+ stocks. Never call them directly at module level — always run in a daemon thread.

**Why:** Running on the main loading thread blocked `_MODULE_FULLY_LOADED = True` (line ~67421)
from being set for 4+ minutes, causing startup_catchup and admin endpoints to fail with 503.

**How to apply:**
- Any new heavy startup computation (DB backfill, bulk indicator compute, API bulk-fetch) must
  go in `_bg_mkt_startup_inits()` or a similar daemon thread wrapper, never inline at module level.
- The `_MODULE_FULLY_LOADED` pattern: set it as the very last line of main.py; startup_catchup
  waits for it (60×10s loop); admin endpoints gate on `globals().get('_MODULE_FULLY_LOADED')`.
- Verify fast load: after any new module-level addition, check admin endpoint responds <75s after restart.

## Companion fix: late-defined dependencies
`_aiem_paper_execute_today` (moved to line ~16731 before dead zone) depends on:
- `_aiem_paper_pick_candidates` (688 lines)
- `_is_trading_day` (12 lines)
- `_aiem_paper_flag_fills` (132 lines)

All three were originally at lines 44215-45052 (after dead zone). The `_MODULE_FULLY_LOADED`
flag guarantees they are in globals before `_aiem_paper_execute_today` is called.
