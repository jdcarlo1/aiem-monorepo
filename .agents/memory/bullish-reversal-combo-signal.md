---
name: Bullish reversal combo signal (candlestick + Parabolic SAR flip)
description: SNDK-style market-wide combo scan wiring — candlestick pattern + same-bar PSAR flip, daily Telegram alert, dedup via signal_fire_log
---

The user's real pre-move signature is not a candlestick pattern alone — it's a bullish
candlestick pattern (bullish_engulfing/hammer/morning_star) landing on the SAME bar that
Parabolic SAR flips from bearish to bullish. A candle-only match is common and weak;
requiring the same-bar SAR flip is what makes it meaningful. Both conditions must be
computed together per-ticker across the whole market in one pass (not two separate
single-ticker checks) — `_mkt_screen_bullish_reversal_combo()` batch-fetches ~60 bars/ticker,
computes PSAR (standard AF step 0.02/max 0.20) and `candlestick_patterns.detect_patterns()`
together, and flags combo only when both align on the latest bar.

**Why:** the user explicitly corrected an over-claim that stocks sharing only the candlestick
with SNDK "have the same pattern" — they don't, unless the SAR flip also matches. Any future
signal built from a user's real trade example must isolate ALL the co-occurring conditions,
not just the most visually obvious one.

**How to apply:** the daily automatic scan is wired through the existing generic
`_OWNER_EMAIL_SCHEDULE` dict (add `"kind": [(h,m)]` and a branch in `_owner_send_now` —
the scheduler loop auto-registers a CronTrigger job per dict entry, no explicit `add_job`
needed). Alert sends via `_tg_send()` and logs every match via `log_signal_fired()` into
`signal_fire_log` (UNIQUE signal_name+ticker+fire_date gives natural per-ticker dedup).
Additionally dedup the Telegram SEND itself with a `SELECT COUNT(*) FROM signal_fire_log
WHERE signal_name=... AND fire_date=...` pre-check, so an app restart after the scheduled
slot never re-sends the same day's alert. Never claim a win rate for a brand-new descriptive
signal like this — say so explicitly and point to AIEM's stat-test tools per
backtest-delegation-rule.
