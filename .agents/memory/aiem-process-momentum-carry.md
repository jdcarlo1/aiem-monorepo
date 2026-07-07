---
name: AIEM Process Momentum Carry Signals
description: Three stacking gap/momentum signals validated last week; 97.7% WR, avg +15%; wired into aiem_process.py and a dedicated website tab
---

# AIEM Process — Momentum Carry Signals (S1b + S1c + S1d)

## The signals (all stack, all use T-1 close_strength from Polygon grouped-daily h/l/c)

| Signal | Condition | Points | Label |
|--------|-----------|--------|-------|
| S1b | gap 15–25% (zone alone) | +5 | gap_sweet_spot |
| S1c | gap 15–22% + T-1 CS ≥ 0.80 | +8 | momentum_carry |
| S1d | gap 15–22% + T-1 CS 0.60–0.79 | +4 | soft_carry |

S1c and S1d are mutually exclusive. S1b stacks with whichever fires.
Top-tier (S1b+S1c) = +13 pts, mid-tier (S1b+S1d) = +9 pts, base (S1b) = +5 pts.

## T-1 close_strength source
Computed in `_polygon_grouped_daily_universe()` from Polygon grouped-daily:
`t1_cs = (close - low) / (high - low)` — stored as `prev_close_strength` in the ticker dict.
Available every morning at 6:55 AM ET warmup, well before 9:30 AM open.

## Backtest results — last week (Jun 29 – Jul 2, 2026)
- 43 total picks (S1c + S1d tiers only)
- 42W / 1L (RPAY -0.2%, only ticker to go negative)
- **97.7% win rate, avg return +15.0% open-to-close**
- S1c: 26 picks, 96% WR, avg +15.2%
- S1d: 17 picks, 100% WR, avg +14.7%

## $1,000/pick simulation (no stop loss, EOD exit)
- $43,000 invested → **+$6,444 profit** (+15.0% ROI)
- 10% stop loss HURT by $170 (TRNR dipped -10.7% then recovered to +7.0%)
- **Recommendation: NO stop loss, hold to EOD**

## Why no stop loss
These stocks closed in the top 20–40% of their range the prior day (T-1 CS gate).
Buyers continue accumulating on intraday dips — stops get hit then the stock recovers.
Only 1/43 picks ever touched -10% intraday, and it closed +7%.

**Why:** T-1 CS acts as an institutional accumulation filter — stocks that close strong
have buyers willing to step in on dips, making stops counterproductive.
