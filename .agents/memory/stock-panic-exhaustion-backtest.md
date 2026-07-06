---
name: Stock Panic Exhaustion Backtest Results
description: Individual-stock panic exhaustion backtest (Jul 2024 - Jul 2026) — full result matrix and verdict
---

## Signal: Individual-Stock Panic Exhaustion

**Function:** `test_stock_panic_exhaustion()` in `aiem_pullback_reentry.py`  
**Admin endpoint:** `/stock-api/admin/run-stock-panic-exhaustion-backtest`  
**Results table:** `stock_panic_exhaustion_results`

## Backtest Results (2024-07-08 → 2026-07-02, min price $5, stop-loss -8%)

| Config | n | WR_stop | WR_5d | Avg% | Stop-out% |
|---|---|---|---|---|---|
| thr=-10%, 11d | 308,674 | 49.3% | 53.7% | +2.74% | 35% |
| thr=-15%, 11d | 175,943 | 46.4% | 53.0% | +3.17% | 42% |
| thr=-20%, 11d | 105,745 | 43.7% | 52.3% | +3.65% | 47% |
| thr=-15%, 5d | 182,184 | 50.4% | 53.0% | +2.23% | 26% |
| thr=-15%, 20d | 167,466 | 40.0% | 53.0% | +3.61% | 57% |
| thr=-15%, SPY<-3% | 46,644 | 59.7% | 63.3% | +5.59% | 30% |
| thr=-20%, SPY<-5% | 17,100 | 58.9% | 67.8% | +7.06% | 31% |

## Key Findings

- **Standalone (no SPY filter): NOT viable.** WR 43–50%, high stop-out rates (35–57%).
- **With SPY panic filter: STRONG.** 59–68% WR, ~30% stop-out, avg +5.6–7.1%.
- **Best combo:** thr=-20% + SPY<-5% → 67.8% WR at 5d, n=17,100 across 2,590 tickers.
- **Interpretation:** This is NOT an independent signal — it amplifies the macro SPY panic signal.
- **Stop-loss:** -8% stop is too tight without SPY filter (kills too many winners). Works fine at ~30% stop-out when SPY filter is applied.
- **Verdict:** Status = hypothesis-confirmed-with-condition. Needs SPY panic regime to be valid. Do not register as standalone signal.

**Why:** FILTER clause syntax bug (can't follow arithmetic ops) fixed by using CASE WHEN in all aggregates + `::numeric` cast for ROUND.
