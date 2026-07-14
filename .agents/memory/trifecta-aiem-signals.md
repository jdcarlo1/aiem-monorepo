---
name: Trifecta AIEM Signals
description: Gap-down >10% backtested signal in 3 volume tiers — named by user, sent to Telegram at 9:37 AM ET
---

## What it is
Three backtested tiers of the same Gap-Down >10% same-day pattern (buy at 9:30 AM open, sell at close):

| Tier | Condition | Avg Return | Backtest EV |
|------|-----------|------------|-------------|
| 1 (ELITE) | Gap dn >10% + Vol >5M | +9.1% avg | +$910/trade |
| 2 (STRONG) | Gap dn >10% + Vol >1M | +6.1% avg | +$610/trade |
| 3 (BASE) | Gap dn >10% (any vol) | +4.2% avg | +$420/trade |

Optimal stop: 3%. Buy strategy: immediately at 9:30 AM open (not waiting). Signal is RARE — most days nothing fires.

## Implementation
- **Function**: `send_trifecta_signal_alert()` in `aiem_telegram_notifier.py`
- **Schedule**: 9:37 AM ET Mon-Fri (CronTrigger, job id = `aiem_trifecta_signal_alert`)
- **Data source**: `aiem_first_candle_data` table — `premarket_gap_pct <= -10`
- **Volume tier proxies** (first 5-min candle ≈ 8-12% of full-day volume):
  - Tier 1: `first_candle_volume >= 400,000` → proxy for >5M daily
  - Tier 2: `first_candle_volume >= 80,000` → proxy for >1M daily
  - Tier 3: any gap-down >10% with any volume
- **Idempotency**: `aiem_notifier_log` with `brief_type='trifecta'` — claim-before-send, never sends twice
- **Fires only** when ≥1 hit exists — completely silent on days with no qualifying stocks

**Why:** User explicitly named these "Trifecta AIEM Signals" and requested Telegram alerts whenever they arise; they are rare but high-conviction same-day plays from the July 2026 backtest.

**How to apply:** Do not rename or split the three tiers. Do not merge with the RVOL combo brief. The 9:37 AM timing is intentional — first-candle module writes at 9:36 AM so one minute of buffer ensures data is ready.
