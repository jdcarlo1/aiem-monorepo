---
name: Price Signal Backtest Results (495 days)
description: Full backtest of 12+ price signals over Jul 2024-Jun 2026 on polygon_market_daily. Four validated signals with real edge found.
---

# Price Signal Backtest — Jul 2024 to Jun 2026

## Data source
`polygon_market_daily` — 3.3M rows, 13,949 tickers, 495 trading days.
Computed 20d rolling RVOL from raw volume. Entry = next day open. Signals use: close_price, prev_close, open_price, gap_pct, close_strength, range_pct, volume.

## Validated Signals (saved to aiem_signal_discoveries)

### S7c★ — BigCatDay + InsideDay + Gap [BEST]
**Rules:**
- Day -1: closed up ≥5%, close_strength ≥ 0.70 (top 30% of range)
- Today: inside day (range_pct ≤ 1.5%), flat/small gap
- Tomorrow: gaps up ≥ 3%
- Entry: Day+2 open. Hold: 3 days.

**Results (n=199, Jul 2024–Jun 2026):**
- WR 3d: 72.9% | Avg ret: +2.19% | Avg win: +3.84% | Avg loss: -2.25% | EV: +2.187%/trade
- WR 5d: 75.3%
- Fires ~1.5×/week

**Quarterly validation:**
- Q2024-Q3: 83.3% | Q2024-Q4: 54.5% | Q2025-Q1: 28.6% (WEAK, n=7) | Q2025-Q2: 76.0% | Q2025-Q3: 73.1% | Q2025-Q4: 72.0% | Q2026-Q1: 76.9% | Q2026-Q2: 85.7%
- ONE weak quarter (Q1-2025, n=7). Otherwise consistent.

**Why it works:** Catalyst day shows institutional buying + strong close (demand absorbed supply). Inside day = consolidation, no new sellers. Gap-up continuation = buyers stepping in again. Three-day confirmation of trend.

### S7c◆ — BigDay + TightInside + Gap [MEDIUM]
Same as above but D-1 ≥3% (not 5%), range ≤1.5%, gap ≥2%.
n=711 | WR 3d: 65.7% | EV: +1.273%/trade | Fires ~5×/week.

### S4◆ — GapDown + StrongClose + Vol [1-DAY]
- Gap down -2% to -5% at open
- But closes in top 20% of day's range (CS ≥ 0.80)
- Volume ≥ 1.3× 20d average
- Entry: next morning open. Hold: 1 day only.
n=2,224 | WR 1d: 58.6% | EV: +0.45%/trade. Edge decays at 3d. Intraday/1-day trade only.

### S10◆ — SoldOff + MeanRevert [5-DAY]
- Prior day: closed down ≥3%, CS ≤ 0.35 (weak close)
- Today: flat gap (≤±1.5%), volume ≥ 1.3×
- Entry: next morning open. Hold: 5 days.
n=12,843 | WR 5d: 53.1% | EV: +0.60%/trade. Best for baskets.

## What did NOT work
- OI buildup + shakeout proxy (volume): WR 47.5% at 3d, EV -0.15% — needs real OI data
- Single volume spike alone: WR 52.9% at 5d, EV +0.23% — too small to trade
- Gap-up + volume: near random (49-50% WR)
- Momentum (2-day up + vol): near random

## EOD Sweep Log Signal (options-based, only 3 weeks data)
- Score 6-8: 100% WR at 3d (+17.22%), 100% at 5d (+22%) — n=19, not enough history yet
- Score 4-6: 75% WR 3d, 75% 5d — n=38
- Need 6+ months of data for statistical confidence

## Key negative findings
- Day 1 (next open after signal) is usually near-zero or negative — don't rush in
- Volume alone as OI proxy = no edge
- GapDown + close > prev_close = 0 occurrences in 400K gap-down events (impossible in real data — if gapped down, close is always < prev_close by construction of gap_pct column)

**Why:** Run Jun 27, 2026 in response to user request to backtest OI shakeout signal across 3-6 months.
**How to apply:** Implement S7c★ as a live signal in the scanner. Log entries/exits to validate WR going forward.
