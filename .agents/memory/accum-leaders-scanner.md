---
name: Accumulation Leaders scanner
description: Shakeout→reentry pattern + options sweep cross-confirm; backtest, architecture, filter values
---

## Backtest Result (30 days, 12K stocks, 45d window)
- Pattern alone: 49% WR, -0.35% EV/trade (coin flip — do NOT trade)
- Pattern + options sweep (±2d): **76% WR, +2.19% avg 5d EV** (47 signals)
  - Avg win +4.3%, avg loss -4.6%, R:R 0.95x
  - Examples: ELVN +12%, CART +9%, BROS +9%, XBI +9%

## Signal Architecture
The shakeout identifies LOADING (institution accumulating 5-7 days quietly).
The sweep confirms DONE LOADING (smart money buys calls = betting on the run).
Together = 76% chance of 5-day gain. Alone = coin flip.

## SQL Filter Values (validated against backtest)
- close_strength ≥ 60% on 3+ of last 7 days (high_cs_days ≥ 3)
- Deep shakeout: cs_min_mid (days 3-5) ≤ 25%
- Strong re-ignition: cs_best_recent (last 2 days) ≥ 70%
- Yesterday CS ≥ 60%
- Avg intraday range < 6%
- Dollar volume ≥ $2M/day, stock price $8-$300
- RVOL recent (last 2d) > RVOL older + 0.05
- Price now ≥ 97% of 7d-ago price (uptrend)
- Positive gap days ≥ 1
- Sweep: unusual_calls_log prem ≥ 50 within ±3 days (LEFT JOIN)

**Why:** Tighter filters removed false positives. Sweep cross-confirm is the KEY gate.

## Email Design
- Section 1: ⚡ HIGH CONVICTION (sweep confirmed, green highlight)
- Section 2: 👁 WATCH LIST (pattern only, enter when sweep fires)
- 8:40 AM ET pre-market send

## Implementation Files
- Backend: `_run_polygon_accum_scan()` in main.py
- Email: `_send_accum_leaders_email()` in main.py
- Endpoint: `/stock-api/grinder-scan` → GrinderResult interface
- Frontend tab: SteadyGrinder in Dashboard.tsx
