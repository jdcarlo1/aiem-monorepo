---
name: AIEM Process alert system
description: S1b/S1c/S1d Telegram alert pipeline — threshold, format, dedup, and scoring constraints
---

## Confidence threshold
- Set to **50** (not 72, not 45)
- Max achievable score without float/SI/catalyst data is ~61% (scoring uses raw_score/max_score × 100 and max_score always accumulates ALL signal bases)
- At 50: only S1b/S1c/S1d validated picks pass; gap_large (10-15%) tops out at ~48% and is blocked
- At 47-49: worst of both worlds — drops MNTS-type winners but keeps DRAL-type losers
- Backtest Jul 6 2026: threshold 50 → AMCI only +21% (100% WR); threshold 45 → AMCI+DRAL+MNTS (67% WR, 1 loser)

## Signal scoring tiers (without float/SI data)
| Signal | Condition | Score |
|---|---|---|
| S1c momentum_carry | gap 15-22% + prev_cs ≥ 0.80 | ~60.6% |
| S1d soft_carry | gap 15-22% + prev_cs 0.60-0.79 | ~57.5% |
| S1b gap_sweet_spot | gap 15-25% | ~54.3% |
| gap_large (invalid) | gap 10-15% | ~47.2% |

## Alert format
- One grouped message per day at 9:30 AM ET (NOT per-pick)
- Grouped: 🟢 S1c → 🔵 S1d → 🟡 S1b
- Dedup: `signal_fire_log` row with ticker='DAILY_SUMMARY', signal_name='AIEM_OPEN_ALERT'
- `if "DAILY_SUMMARY" in already: return` at top of open_watcher — hard exit

## Polygon 403
- `/v2/snapshot/locale/us/markets/stocks/tickers?tickers=...` always 403 on current plan
- Open watcher falls back to `_td_quotes()` (Tradier) — returns price/prev_close/volume/avg_volume ✅

## prev_close_strength in live re-score
- Tradier does not return prev_close_strength
- Fix: infer from premarket signal_basis string at open time
  - "momentum_carry" in sig_basis → inferred_prev_cs = 0.85
  - "soft_carry" in sig_basis → inferred_prev_cs = 0.70
  - else → 0.0

## Holiday calendar
- `_US_HOLIDAYS_2026` set at module level (requires `from datetime import date`)
- `_market_day()` checks weekday < 5 AND date not in holiday set

## Key bug that was silent
- Original threshold 72 was unreachable — zero alerts would ever have fired
- Discovered via scoring simulation: max achievable = 61% (S1c) without float/SI data
