---
name: Morning Smart-Money idea emails (cap-split)
description: The 9:05 ET pre-open Smart-Money-Pressure idea-email family — one engine run, three cap-bucket owner emails; why thresholds/locks/boundaries are what they are.
---

# Morning Smart-Money Pressure idea emails (cap-split)

A MORNING owner-email family that scores optionable stocks with the existing L1-L8
"Smart Money Pressure" conviction engine (`_run_five_layer_conviction`) and splits the
ranked output by market cap into THREE SEPARATE owner emails: Small ($300M-$2B), Mid
($2B-$10B), Large ($10B+). Framed as idea generation ("gives us an idea"), NOT a sized
buy list. Owner-email kind `smp_morning`, slot 9:05 ET. Sibling of the nano/sc morning
systems (see sc-morning-system.md, nano-morning-system.md) but reuses the smart-money
engine rather than a bespoke ranking.

## One engine run -> up to 3 emails
The `smp_morning` slot claims ONCE in `owner_email_log`, runs the engine ONCE, then sends
up to three cap-bucket emails. Empty buckets stay silent (logged). Do not split this into
three slots — that would run the heavy engine three times and triple the lock contention.

## Threshold is total_pts >= 4 for MORNING ONLY — deliberately lower than EOD/intraday (>=6)
**Why:** the 9:05 pre-open read is built on fresh premarket OI + prior-day sweep/charm/
sector context, but intraday L2 gamma builds AFTER the open, so morning scores run lower.
A >=6 gate would suppress almost all small/mid-cap ideas pre-open and defeat the purpose.
**How to apply:** the existing EOD (16:50) and intraday smart_money emails MUST stay >=6;
only the morning path uses `_SMP_MORNING_MIN_PTS=4.0`. Never lower the EOD/intraday gate to
match — they have full gamma and >=6 is the proven bar.

## Shared card renderer `_smp_build_cards(signals)` is the seam — guard the EOD path
Both the existing `_send_smart_money_pressure_email` and the 3 morning emails render via
`_smp_build_cards`. The helper has tier branches for EXTREME(>=8)/HIGH(>=6)/MODERATE(4-5.9).
`_send_smart_money_pressure_email` pre-filters its `signals` to `total_pts >= 6` BEFORE
calling the helper, so the MODERATE branch is unreachable there — the EOD email is unchanged.
**How to apply:** any edit to `_smp_build_cards` must preserve byte-behavior for >=6 inputs;
do NOT remove the >=6 pre-filter in the EOD sender or MODERATE cards will leak into it.

## Lock: acquire ONLY in `_owner_send_now`, never in the sender
`_CONVICTION_SCAN_LOCK` is a plain `threading.Lock` (NOT reentrant). The `smp_morning`
branch in `_owner_send_now` acquires it (with timeout) and releases in `finally`;
`_send_smp_morning` assumes ownership and must NOT re-acquire (would self-deadlock). This
mirrors the existing `smart_money` pattern. Lock-busy after the slot is claimed => skipped
for the day (acceptable; same as smart_money).

## Cap boundaries: inclusive-lo / exclusive-hi; drop unknown + <$300M
`300M <= small < 2B`, `2B <= mid < 10B`, `>=10B` large. Names with unknown cap or <$300M
(nano/micro) are dropped entirely — better to omit than misbucket. Caps come from
`_microcap_meta` first (only reliably covers sub-$2B), else yfinance `fast_info.market_cap`
(snake_case — see yfinance-fastinfo.md), 10 workers, ~30min `app._smp_cap_cache`.

## Do NOT add a nano/micro bucket to this family — it's covered separately
**Owner-confirmed (June 2026):** nano-caps are intentionally NOT a 4th bucket here. The
SMP engine ranks by OPTIONS activity (OI/charm/gamma) and nano/micro names have no listed
options, so this engine would never surface them. Nano is covered by its OWN dedicated
morning system (nano_watch 9:35 + nano_buy 9:45) scored on non-options metrics — stealth
accumulation + volume + momentum + low float (see nano-morning-system.md). If asked to
"add the nano tab" to the morning emails, the answer is: it already exists as a separate
email; do NOT bolt a nano bucket onto smp_morning.

## Option-chain cost guard
`_scan_best_call` (live chain lookup) only fires for CALL recs, and `_expiry_recommendation`
returns CALL only for score>=6. So MODERATE (4-5.9) morning ideas never touch the chain API —
keeps the lower morning threshold cheap. Don't "improve" this by fetching chains for MODERATE.

## Delivery: email-only (no ntfy)
Morning path sends 3 emails; intentionally no ntfy push (would be 3 phone pushes pre-open).
Consistent with the alert-delivery rule (sms-delivery-solution.md): email is the channel.
