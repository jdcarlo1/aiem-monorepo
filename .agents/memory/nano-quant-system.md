---
name: Nano Quant Z-Score System
description: Production quant z-score system replacing V2 as the primary nano-cap morning alert scorer
---

# Nano Quant Z-Score System

## What it is
5-factor cross-sectional z-score system that ranks each morning's nano-cap candidates relative to each other (not against fixed thresholds). Replaced V2 as the primary alert filter.

## Factors and weights
- gap_z: gap% vs pool (20%)
- mom_z: avg of pct_z(mom10) + pct_z(mom5) (30%)
- qual_z: z-score of `steady` field (smoothness proxy) (20%)
- ft_z: float turnover = (vtrend × avg_vol) / float_shares × 100 (15%, optional)
- sq_z: short_pct × (1/short_ratio) (15%, optional)
- ft and sq weights redistribute if data missing

## Grading
- STRONG: top 15% composite_z OR composite_z ≥ 0.75
- WATCH: top 30% OR composite_z ≥ 0.25
- SKIP: everything else

## Backtest results (Apr 1 – Jun 18 2026, 54 trading days)
- 34 of 54 days regime-gated (IWM day≤-1%, 5d≤0%, or 20d≤0%) — sat out correctly
- 20 active trading days
- Quant alone: 40 trades, 48% WR, +$1,199, +3.0%/capital
- V2 alone: 141 trades, 35% WR, -$656, -0.5%/capital

## Implementation location
- Scoring: `_run_nano_morning_ranking()` in main.py — after V2 per-ticker pass, fetches float/SI for top 60 in a second parallel pass (max_workers=8), computes z-scores, stores all quant fields in `r` dict which is JSON-dumped as the `meta` column
- Buy filter: `_send_nano_buy_email()` — reads `meta.quant_grade == "STRONG"`
- Watch email: `_send_nano_watch_email()` — shows composite_z + factor breakdown
- Both emails fire at 8:30 AM ET

## Email schedule
- 8:00 AM ET: ranking scan (nano universe ~700 tickers, 40d history)
- 8:30 AM ET: watch email (all ranked candidates with z-scores) + buy email (STRONG only)

## Key design decisions
- Cross-sectional (relative) scoring, not absolute thresholds — adapts to each day's pool
- Second parallel pass for float/SI avoids slowing the per-ticker V2 pass
- If quant z-score computation fails (non-fatal), falls back to V2 sort
- STRONG = top 15% of pool, consistent with backtest threshold

**Why:** V2 LOSES money at scale (-0.5%/capital over 2.5 months). Quant correctly rejects 89% of V2 signals — the ones averaging -0.7%/capital.
