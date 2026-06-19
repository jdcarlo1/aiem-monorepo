---
name: Nano V2 filter gates (backtested Jun 9-13 2026)
description: Three hard gates added to V2 nano-cap scoring after backtest; win rate, EV before/after, and one remaining open problem.
---

# Nano V2 Filter Gates

## The three gates (all live in `_score()` / `_send_nano_buy_email()`)

1. **RVOL < 3x → `return None`** (hard gate in `_score`)
   Low-RVOL names had 14-45% win rates — no real volume = no signal.

2. **RVOL > 60x → `return None`** (hard gate in `_score`)
   Extreme volume is pump-and-dump exit, not entry. EBON 230x lost -9.9% Jun 11.
   Combined with gate 1: only RVOL 3–60x passes.

3. **IWM ≤ -1% at open → suppress buy list** (in `_send_nano_buy_email`)
   Sends a "suppressed" email instead of buys. Uses `fast_info.last_price / fast_info.previous_close`.

## Backtest results Jun 9-13 2026

| | No filters | All 3 filters |
|--|--|--|
| Signals | 73 | 19 |
| Win rate | 41% | **58%** |
| Avg return | -0.7% | **+0.1%** |
| EV/$500 | +$2.25 | **+$6.97** |
| Stopped out | 17 | 4 |

Jun 10 correctly sat out (IWM -1.04%). Jun 13 had no intraday data (too recent).

## Remaining open problem — Jun 11
IWM was UP +2.96% but 5/7 nano STRONG signals lost (avg -4.9%). The losers all had:
- mom10 > 19% AND gap 6-8% — already extended before the day
- Next candidate filter: if mom10 > 18% AND gap > 5% → downgrade or skip (overextended)
**Do NOT add this yet** — need more live data before adding another rule to avoid overfitting.

**Why:** Jun 11 may be a one-off sector-specific selloff unrelated to scoring inputs.
**How to apply:** Re-examine after 3 weeks of live data. If win rate on mom10>18%+gap>5% combos stays below 40%, add the gate.

## RVOL is intraday (9:30-9:45 opening range), not EOD
The live system uses EOD vol5/vol20. The backtest used real 1-min opening range volume
(first 15 min vs avg_daily / 26). Real intraday RVOL is more accurate — EOD backtests
systematically undercount volume interest on gap-up days. Trust live system RVOL readings.
