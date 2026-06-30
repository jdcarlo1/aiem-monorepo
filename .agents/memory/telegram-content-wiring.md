---
name: Telegram content wiring
description: How owner alert emails also send content to Telegram — the "sending now" vs actual-data split and the fix
---

## The pattern

`_owner_send_now(kind)` sends a "sending now..." ping to Telegram (via `_tg_send()`), then calls the actual sender function (e.g. `_send_high_conviction_email`). The sender functions send HTML to the owner's email via `send_email_raw()`. **They do NOT send content to Telegram on their own.**

To get actual picks into Telegram, each sender function needs its OWN `_tg_send()` call after `send_email_raw()`.

## Wired functions (as of Jun 30 2026)

All 11 sender functions now call `_tg_send()` with a plain-text summary after their `send_email_raw()` call:

- `_send_polygon_rvol_email` — top 8 RVOL movers
- `_build_market_brief_html` — bottom-line bullet list (called inside _send_market_brief_email)
- `_send_nano_watch_email` — top 6 candidates with quant z-score
- `_send_nano_buy_email` — all STRONG buys (entry/stop/z)
- `_send_sc_watch_email` — top 6 with double-signal / calls badges
- `_send_sc_buy_email` — confirmed buys (entry/stop/blended score)
- `_send_smart_money_pressure_email` — top 6 signals scored /10
- `_smp_send_morning_bucket` — per-cap bucket (Small/Mid/Large) ideas
- `_send_microcap_calls_email` — top 5 (strike/expiry/vol-OI/premium)
- `_send_high_conviction_email` — top 5 (score/sweeps/premium/urgency)
- `_send_accum_leaders_email` — sweep-confirmed + watch list

## Rules

- All `_tg_send()` calls are wrapped in `try/except` — a Telegram failure must never block email delivery.
- Telegram message format is plain text (no HTML), ≤4096 chars. Use compact per-ticker lines (2 spaces indent).
- Adding a new owner email kind: add `_tg_send()` in the sender function itself, NOT in `_owner_send_now()`. The status ping in `_owner_send_now` is separate.

**Why:** `_owner_send_now` only had the pre-notification ping. The content senders were email-only by design, so Telegram showed ~10 "sending now" messages with zero follow-up data.
