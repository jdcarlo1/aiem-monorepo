---
name: Multi-Day Runner quality filters
description: Three data-validated signal filters applied to all cap tiers in multiday_runner.py, sourced from 3-month backtest June 2026
---

# Multi-Day Runner Quality Filters

Implemented in `multiday_runner.py` as `EXTREME_CAP` and `WEAK_PRICE_ZONE` constants, applied in both `run_intraday_d1_scan()` and `run_day1_scan()`.

## Filter 1: Monday skip (all tiers)
Skip all signals fired on Monday (Python `today.weekday() == 0`).

**Why:** Monday WR across tiers: large 52.6%, mid 36.5%, small 46.9% vs rest-of-week 55-57%. Weekend news fully priced in at Monday open; no institutional follow-through on Day 2.

**How to apply:** Hard gate in both scan functions before appending to candidates/saving to DB.

## Filter 2: Extreme gain cap
- Large cap: skip if D1 gain > 15%
- Mid cap: skip if D1 gain > 15%
- Small cap: skip if D1 gain > 17%

**Why:** Binary events (earnings/FDA/M&A). Large/mid >15% WR ~48%; small >17% WR 41.1%. Move is spent, no continuation.

**How to apply:** Check `d1_pct > EXTREME_CAP[tier_key]` after the min_pct gate.

## Filter 3: Weak price zone (mid/small only)
Skip if stock price is $15-$50 for mid cap or small cap. Large cap has no price filter.

**Why:** Mid $15-$50 WR=45.7%, avg=+0.02%. Small $15-$50 WR=38.5%, avg=-0.97%. These are "fallen large caps" with institutional overhead supply preventing continuation. Both under $15 and over $50 outperform in mid/small.

**How to apply:** Check `WEAK_PRICE_ZONE[tier_key]` — if set, skip if `min <= price < max`.

## UI
Monday banner appears in MultidayRunnerTab (Dashboard.tsx) when `new Date().getDay() === 1`.
Empty state text is also Monday-aware.

## Expected impact
- Mid cap WR: 50.5% → ~55-57% (Monday alone was dragging it down ~14pp)
- Large cap WR: 55.9% → ~57-58%
- Small cap WR: 51.6% → ~54-55%
