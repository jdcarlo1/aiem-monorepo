---
name: Short Calls grading NameError fix
description: _parse_expiry_date had `_dt` undefined causing grading to fail 100% of the time; outcome logic also only resolved from newly-fetched data
---

## The bug
`_parse_expiry_date` imported `date as _d, timedelta as _td` but used `_dt.strptime(...)`.
NameError on every call → `_update_ai_short_call_outcomes` crashed entirely → 0 picks ever graded on prod.

**Fix:** `from datetime import date as _d, datetime as _dt, timedelta as _td`

## Second bug: outcome stuck OPEN
Outcome was only set WIN/LOSS when `expiry_win` or `t5_price` was *newly fetched* in the current pass.
Picks that had t1_win/t3_win from a previous pass but t5/expiry still unavailable stayed OPEN forever.

**Fix:** Read existing t1w/t3w/t5w/exp_w from DB row; resolve outcome from all available data using
priority: `expiry_win > t5_win > t3_win (if expiry past) > t1_win (if expiry well past)`.
Write outcome update even when `updates` dict is empty (outcome != "OPEN" is sufficient trigger).

## Startup catch-up
Added `_startup_grade_short_calls()` at module level — fires `_update_ai_short_call_outcomes()` 30s
after boot so fresh deploys auto-grade the backlog without waiting for 4:32 PM scheduler.

**Why:** Prod had 413 OPEN picks, grading never worked → startup trigger + correct code = instant fix on republish.
