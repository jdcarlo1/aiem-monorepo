---
name: Momentum signal macro regime research
description: Backtested results of sector breadth, short interest, and market regime filters on confirmed coil+breakout events
---

# Momentum Macro Regime Findings

## Backtest
- 472 confirmed coil+breakout events (2024-2026, 20% ticker sample)
- Baseline: WR=64%, avg 60d=+27.5%, catastrophic loss=21%

## Key Finding — COUNTERINTUITIVE
Bear regimes produce BETTER outcomes for the coil+breakout signal:

| Regime | WR | Avg 60d | Catastrophic Loss | N |
|---|---|---|---|---|
| BEAR (breadth≤30% OR SPY<20d) | 69% | +33.9% | 18% | 187 |
| NEUTRAL | 63% | +27.5% | 21% | 78 |
| BULL (breadth≥50% + SPY+QQQ>20d) | 63% | +23.7% | 21% | 207 |
| VERY_BULL (breadth≥70% + all 3) | 61% | +22.3% | 25% | 122 |

**Why:** In a bull market, stocks drift up with the tide — a coiling stock is likely an underperformer.  
In a bear market, a stock that coils and breaks out is doing so AGAINST headwinds = real underlying demand.

**How to apply:** Do NOT suppress momentum_trade_score signals in bear regimes. Keep firing.

## Filters Tested and Rejected
- Macro bullish filter → HURTS signal (loses 40% of events, no win rate improvement)
- NOT bear filter → HURTS signal (cuts the best-performing regime)
- Sector breadth continuous correlation: rho=-0.091, p=0.049 (negative — higher breadth = slightly worse)

## Short Interest
- polygon_short_interest: only 2% of coil events had any SI data (too sparse)
- n=9 events with SI data — statistically meaningless, cannot backtest

## Earnings Calendar
- earnings_calendar table is EMPTY — cannot test earnings proximity filter

## AIEM Wiring (July 2026)
- New tool: `momentum_macro_regime` — tool map line ~29726, schema line ~31198, wrapper line ~56566
- Fixed pre-existing: `momentum_optimize_filters` now has schema entry (line ~31218)
- Tool returns: regime label, sector breadth, sector detail per ETF, index positions vs 20d/50d MA, regime_comparison table
- Current regime as of July 5 2026: VERY_BULL (9/11 sectors above 20d, SPY✓ QQQ✗ IWM✓)

## Price/Volume Ceiling Reached
After breakout confirmation fires, the remaining losers (10.7% catastrophic) cannot be filtered by OHLCV data:
- RSI, BB width, vs_ma200, above_ma200, relative strength vs SPY: all p>0.05 (noise)
- Only vol_vs_20d (p=0.011) and low_stability (p=0.017) show marginal signal — optimal cuts keep only 26-28% of winners
- The breakout confirmation IS the primary filter. Further improvement requires non-OHLCV data (news, fundamentals, SI%)
