---
name: Module L Panic Exhaustion rebuild
description: Module L (Pullback Re-Entry) rebuilt to use SPY 20d < -5% as primary gate; RSI removed entirely; 11-day hold target; Telegram alert at 4:30 PM ET
---

## Rule
Module L only fires when SPY 20-trading-day return < -5.0% (SPY_20D_PANIC_THRESHOLD constant).
RSI is computed and stored for reference but is NOT a gate or scoring factor.
State is now always "PANIC_EXHAUSTION" (WATCHING/CONFIRMED removed).

## Why
Backtest on 33,578 rows proved RSI is noise: outside panic window, best indicator peaks at 55% WR.
Inside panic window (SPY 20d < -5%), ALL RSI buckets produce 83-100% WR — the macro condition IS the signal.
Panic window evidence: n=1,763, 85% WR (5d), 88% WR (10d), avg +11.1% (10d).
User target: hold 11 trading days from entry.

## Key stats (2026 tariff selloff, only confirmed instance)
- 20-trading-day lag = exactly 28 calendar days
- Best combos: PRIOR_BREAKOUT support (90% WR) + EXPANDING volume (87% WR)
- All indicators cluster within 83-94% WR inside panic — spread only 11pp
- Peak days: Mar 27/30 = 91% WR, 96% WR at 10d, avg +16.8%

## Telegram alert (4:30 PM ET daily, mon-fri)
- ENTERING: full instructions + 5d/10d/11d stats
- ACTIVE: daily reminder with hold instruction
- EXITING: when SPY 20d recovers above -3% (hysteresis)
- Table: aiem_panic_exhaustion_log (check_date PK, tg_sent dedup)
- Admin endpoint: POST /stock-api/admin/check-panic-exhaustion

## Conviction scoring (inside panic window)
- Baseline: 7
- PRIOR_BREAKOUT support: +2
- EMA21 support: +1
- EXPANDING volume: +1
- LIGHT volume: -1
- RS WEAKENING: -1

## Warning
Only one historical instance (Apr 2026). Signal needs second selloff to validate.
Do not lower SPY_20D_PANIC_THRESHOLD without re-running backtest.
