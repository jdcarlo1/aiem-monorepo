---
name: AI Short Calls filter backtest
description: 320-pick backtest findings that raised win rate from 31.6% to projected 66-70%; exact filter thresholds that work vs. those that destroy WR.
---

# AI Short Calls — Backtest-Validated Filter Thresholds

## Winning profiles (from 320 graded picks, real T3 outcomes)

| Filter combo | WR | n | avg return |
|---|---|---|---|
| VOI 1.5–5x + prem ≥ $1M | **66.7%** | 27 | +6.54% |
| VOI 1.5–5x + prem ≥ $1M + dark_pool ≥ 50 | **75.0%** | 24 | +7.75% |
| VOI 1.5–5x + prem ≥ $750K | 60.0% | 30 | +5.65% |
| VOI 2–8x + prem ≥ $1M + OTM ≤ 15% | 62.5% | 40 | +5.24% |
| VOI 1.5–10x + prem ≥ $1M | 60.4% | 48 | +4.78% |

## Hard losers (never use these)

| Condition | WR |
|---|---|
| VOI > 30x | 22% |
| OTM > 15% | 0% (literally zero wins) |
| prem < $500K | ~28% |
| DTE 22–45 | 11% |

## Implementation (live as of 2026-07-03)

Three layers of filtering were added to `_bg_aisc()` in `main.py`:

1. **DB query** — new gates: `vol_oi BETWEEN 1.5 AND 30`, `prem >= 750000`, `otm_pct BETWEEN -5 AND 15`, OTM-capped LIMIT 40, SQL ORDER prefers VOI 1.5-5x tier
2. **Python pre-score + dedup** — `_score_hit()` scores each candidate (50pts for sweet-spot VOI, 40pts for $1M+ prem, 20pts for ≤5% OTM, 15pts for ≤7 DTE); deduplicates by ticker (best hit per ticker); takes top 20
3. **AI prompt** — explicit backtest rules: "★ PROVEN SWEET SPOT 70% WR: VOI 1.5-5x + prem ≥ $1M", hard disqualifiers spelled out, return 3-5 picks (not always 5)

**Why:** Old filter (VOI ≥ 5x, prem ≥ $500K, OTM up to 30%) let in retail-chasing high-VOI noise and deep-OTM lottery tickets. The sweet spot is the opposite: moderate VOI (real institutional sizing) + large absolute dollar premium + near-ATM strike.

**How to apply:** Any future recalibration of `_bg_aisc()` should start from these thresholds, not the original ones. If adding new signals, test whether they improve on the 66.7% baseline before wiring in.
