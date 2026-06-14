---
name: Scanner baseline performance — June 14, 2026
description: Backtested win rate, R:R, and EV/trade for all scanners as of June 14, 2026. Compare against live data in ~4 weeks (mid-July 2026).
---

## Baseline Snapshot — June 14, 2026
Backtest window: June 1–13, 2026 (10 trading days)
Filters applied: all new gates added June 14 (gap cap, VWAP ext cap, gain-from-open cap, XLV/XLY block)

### Morning Burst (9:35 / 9:40 / 9:45 AM ET)
- Win Rate: ~71% (est. 25 signals from 40 raw)
- Avg Win: +2.55%
- Avg Loss: -3.33%
- R:R: 0.77:1
- EV/trade: +0.85%
- Notes: Sub-1 R:R offset by high WR. New gates added June 14: gap_pct > 4% skip, vwap_ext > 2% skip.

### Steady Grinder (10:30 AM–1:30 PM ET, every 30 min)
- Win Rate: 68.8% (16 signals, filtered from 28 raw)
- Avg Win: +1.50%
- Avg Loss: -1.65%
- R:R: 0.91:1
- EV/trade: +0.54%
- Notes: New gates added June 14: gain-from-open < 3%, block XLV + XLY sectors.

### Pre-Close Swing (2:00 PM ET scan, D+1 close exit)
- Win Rate: 71% (n=7 — THIN, not statistically reliable yet)
- Avg Win: +3.97%
- Avg Loss: -9.47%
- R:R: 0.42:1
- EV/trade: +0.07%
- Notes: Very small sample. 71% could easily be 50% or 85% with more data. Exit rule: D+1 close only (D+3 had 0% WR in tech-correction week).

### High Conviction Options (EXTREME + HIGH tier, 1–30d expiry)
- Win Rate: 91% (n=11 HIGH signals from 45 expired Jun 12 contracts)
- Avg Win: UNKNOWN — not tracked yet (conviction_calls_outcomes table started June 14)
- Avg Loss: UNKNOWN
- R:R: UNKNOWN
- EV/trade: UNKNOWN
- Notes: Only HIGH conviction signals sent (vol/OI ≥5x, prem ≥$500K). MEDIUM tier had 59% WR (n=34).

### Blended Average (all four)
- Win Rate: 75.5%
- EV/trade: Cannot compute until HC options R:R is known

## 4-Week Comparison Target: ~July 14, 2026
Re-run this comparison with live production data from June 14 onward.
Key questions to answer by then:
1. Does Morning Burst hold 70%+ WR in live trading (not backtest)?
2. Does Grinder EV/trade stay positive with new XLV/XLY + gain-from-open gates?
3. Does Pre-Close Swing WR converge toward 50-60% or hold at 70%+? (n=7 is too thin)
4. What is HC Options avg win, avg loss, and R:R once D+1/D+3/D+5 data fills in?
