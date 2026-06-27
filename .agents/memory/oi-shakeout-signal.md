---
name: OI Buildup + Shakeout → Reentry Signal
description: New alert built from backtest finding — 75% WR, +8.20% EV/trade (n=28); wired into 8:45 AM + 9:52 AM scheduler slots.
---

# OI Buildup + Shakeout → Reentry Signal

## Backtest result
75% WR, +8.20% EV/trade, n=28. Strongest signal in the backtest session (Jun 27, 2026).

## Pattern
1. Smart money loads OI for 2+ consecutive days with no price appreciation (stealth).
2. Then price fakes a breakdown (shakeout): d1 price drops below d2 price ≥ 0.5%.
3. OI stays elevated through the dip — institutions bought the weakness.
4. Entry: price reclaims prior close after the shakeout.

## Data source
`oi_daily_snapshot` table (written at 4:30 PM + 8:30 AM pre-market daily).
- d1 = most recent snapshot (yesterday EOD)
- d2 = 2nd most recent
- d3 = 3rd most recent
- Filter: OI d3→d2→d1 all increasing, ≥30% total buildup, d1 price < d2 price × 0.995

## Functions (in main.py near line 11804)
- `_get_oi_buildup_shakeout_candidates()` — pure DB query, returns list of dicts
- `_send_oi_shakeout_premarket_email()` — 8:45 AM, "Setup Watch" listing; silent when 0 candidates
- `_send_oi_shakeout_reentry_email()` — 9:52 AM, "ENTRY SIGNAL" when any candidate bounces +0.5% above d1 price

## Scheduler slots
- 8:45 AM Mon-Fri: `oi_shakeout_premarket` — lists pre-positioned names
- 9:52 AM Mon-Fri: `oi_shakeout_reentry` — checks Tradier live quotes (no Yahoo risk)

## Dedup
- Pre-market email: `app._oi_shakeout_premarket_sent == today_iso`
- Entry email: `app._oi_shakeout_entry_sent_tickers` (set, per-day, per-ticker)

## Why 0 candidates is normal
Signal fires ~1-3x per week on average (28 hits over months of backtest). Current bull trend means few shakeouts. OI snapshot universe is capped at 150 tickers/day — signal will broaden as more snapshot history accumulates.

**Why:** Built on backtest (Jun 27 session) which showed this combination beats all other filter combos by 20+ pp WR.
**How to apply:** Don't lower the 30% OI buildup threshold or the 0.5% shakeout threshold — both were set by the backtest parameters. Wait for the data; the signal is rare by design.
