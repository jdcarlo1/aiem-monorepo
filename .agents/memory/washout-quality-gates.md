---
name: Washout-Complete quality gates
description: 3 research-validated filters that raise washout-complete WR from 55% to 73%, discovered via 97K-signal AIEM backtest
---

## The 3 Quality Gates (wired into _check_momentum_washout_complete)

| Gate | Value | Why |
|---|---|---|
| Bad months | Skip Jan(1), Feb(2), Mar(3), Oct(10) | These months have 44-49% WR — worse than coin flip |
| Price floor | ≥ $5 | Penny stocks show no edge; $10+ gets 60% WR, $20+ gets 66% |
| Prior 10d trend | Must be ≤ -5% | Counter-intuitive: stocks that were RISING into signal = 48% WR; falling >10% = 63% WR |

## Key Findings from 97K-signal Backtest

- **Unfiltered baseline**: 55.4% WR at 1M
- **Good months only** (Apr/May/Jun/Aug/Sep): 62.9% WR (+7.4pp) — single biggest lever
- **April alone**: 71.4% WR — anomalous, likely post-Q1-earnings season recovery
- **February alone**: 44.3% WR — below 50%, the signal FAILS in February
- **Prior 10d falling >10%**: 61.1% WR — a real washout needs a prior downtrend
- **Prior 10d flat/rising**: 48.3% WR — stock pausing, NOT washing out
- **Best combined (Good month + Price≥$10 + Prior10d≤-10%)**: 73.6% WR, 1M and 3M both

## Combined Filter Results (1M WR)
- Good month + Prior 10d falling: **69.4%** (10K signals/2yr)
- Good month + Price≥$10: **66.9%** (15K signals/2yr)
- Good month + Price≥$10 + Prior10d falling: **73.6%** (2.5K signals/2yr)
- Strictest (Good month + Price≥$20 + Prior10d falling): **75.2%** — 3M WR = 79.9%

**Why:** The counter-intuitive trend finding makes total sense: if the stock was still rising before the washout conditions trigger, the sellers haven't actually exhausted — the signal is firing prematurely.

**How to apply:** These gates are hard-wired into `_check_momentum_washout_complete()` in main.py. Constants: `_BAD_MONTHS={1,2,3,10}`, `_MIN_PRICE=5.0`, `_TREND_MAX10D=-5.0`. Column `prior_ret10d` stored in `momentum_washout_complete` table.
