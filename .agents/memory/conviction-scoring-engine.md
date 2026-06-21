---
name: Conviction Scoring Engine — Multi-Day Runner
description: 4-factor quality score for multi-day runner signals; backtest-validated thresholds and performance metrics
---

# Conviction Score (0–4) in multiday_runner_watch

## The 4 Factors
1. **10d magnet zone**: `0.87 ≤ (d1_close / max_10d_high) ≤ 0.970` (tightened from 0.83–0.975)
2. **20d downtrend reversal**: `trend_20d < -2.0%` (NOT just any negative; -2% is the threshold)
3. **D0 quiet**: `prior_gain < 1.5%` (prior day wasn't already running)
4. **ATR normal**: `0.80 ≤ atr_mult ≤ 2.0` (not a panic spike)

## Confirmed Performance (from backtest_highfactor.py)
- Score 4/4: **66% WR, CI floor 60.4%, n=297** (large cap) — statistically solid
- Score 3/4: **60%+ WR confirmed** across tiers
- Base (no filter): 54.9% WR large, 52.6% mid, 50.3% small

## Key design decisions
- **ORTHOGONAL to d1_strong** — d1_strong measures SIZE of D1 move; conviction_score measures QUALITY of setup
- Both can be true simultaneously (best signals) or either alone
- **Sort order changed**: queries now ORDER BY conviction_score DESC, d1_pct DESC (quality first, not just size)
- Period extended from 25d → 45d for enough history to compute 20d trend reliably

**Why:** 4 factors is the sweet spot — 3-factor CI floor ~57%, 4-factor CI floor 60.4% with n=297. 5+ factors: n shrinks below 50 making results statistically marginal.

## DB column added
`conviction_score INT DEFAULT 0` via migration in `init_multiday_runner_tables()`

## Display
- CV4: purple badge `⭐ CV4` (background #a855f7) in TickerCard + emails
- CV3: dim purple badge `CV3` (rgba(168,85,247,0.25)) 
- CV0-2: shown as grey text only in emails
