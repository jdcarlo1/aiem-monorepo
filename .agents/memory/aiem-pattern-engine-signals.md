---
name: AIEM Pattern Engine Signals
description: Nine multi-day reversal/momentum patterns backtested by AIEM, sent to Telegram at 8:50 AM ET via send_pattern_engine_alert()
---

## What it is
Nine backtested multi-day patterns discovered by AIEM on polygon_market_daily.
These are NOT same-day trades — they are 3-10 day holds. Fires premarket (8:50 AM ET)
so user can enter at today's open based on yesterday's close data.

## Patterns (sorted by win rate)
| Pattern | Horizon | Win Rate |
|---------|---------|---------|
| 12-Day ROC < -10% (Deeply Oversold) | 10d | 81.82% |
| 12-Day ROC < -10% | 5d | 75.07% |
| High ATR >3% (any momentum) | 10d | 75.0% |
| High ATR >3% + Momentum Positive | 5d | 74.47% |
| 10-Day Momentum Positive | 5d | 73.87% |
| Williams %R + MFI Oversold | 5d | 72.01% |
| MACD Bearish + ADX Trending >25 | 10d | 71.53% |
| 10-Day Momentum Positive | 10d | 70.85% |
| Washout — RSI + Stoch + Williams All Oversold | 10d | 67.82% |
| Stoch + CCI Oversold | 5d | 67.09% |
| CMF Outflow + MACD Bearish | 5d | 66.65% |
| Williams %R + MFI Oversold | 3d | 65.22% |
| 12-Day ROC < -10% | 3d | 63.05% |

## Implementation
- **Function**: `send_pattern_engine_alert()` in `aiem_telegram_notifier.py`
- **Schedule**: 8:50 AM ET Mon-Fri (job id = `aiem_pattern_engine_alert`)
- **Data source**: `polygon_market_daily` — last 35 trading days of OHLCV
- **Indicators computed in Python (pandas)**:
  - ROC-12, ATR%-14, 10d momentum, Williams %R-14, MFI-14, RSI-14
  - Stoch %K-14, MACD (12,26,9), ADX-14, CCI-20, CMF-20
- **Minimum data requirement**: tickers need ≥27 rows (MACD needs 26 + 1 diff)
- **Filters**: close_price > $1.00, volume > 100K
- **Idempotency**: `aiem_notifier_log` with `brief_type='pattern_engine'`
- **Silent on no-hit days** — only fires when ≥1 pattern has qualifying stocks

## Message format
```
{emoji} {signal name}  |  {win rate}  |  {hold horizon}
   TICK1  TICK2  TICK3  TICK4  TICK5  TICK6
```
Win rate is explicitly on the same line as the signal name (user requirement).

**Why:** User explicitly requested these 9 patterns alerted via Telegram with win rates visible on the same line; discovered by AIEM in the July 2026 premarket pattern engine backtest (494 days, 14K tickers, 3.3M rows).

**How to apply:** Do not merge with Trifecta alerts. Do not change the 8:50 AM timing (needs to fire before market open so user can act at 9:30). The `_update_notifier_status()` helper is the generic version for all non-trifecta briefs.
