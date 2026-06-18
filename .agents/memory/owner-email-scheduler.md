---
name: Owner-email scheduler wiring (StockScanner)
description: How to add a new scheduled owner-only alert email and the dedup/catch-up convention so it fires once per trading day on both the Reserved-VM real-time path and the wake-up path
---

# Owner-email scheduler convention (artifacts/stock-scanner-api/main.py)

To add a NEW owner-only scheduled email (sent to `_OWNER_EMAIL` only, never to customers):
1. Add `"<kind>": [(h, m), ...]` (ET slot times) to the `_OWNER_EMAIL_SCHEDULE` dict.
2. Add an `elif kind == "<kind>":` branch in `_owner_send_now()` that calls your send function.
3. Write the send function (model on `_send_smart_money_pressure_email` / `_send_accumulation_watch_email`).

That is ALL the wiring. A startup loop iterates `_OWNER_EMAIL_SCHEDULE` and registers one APScheduler cron job per (kind, slot), AND the wake-up catch-up `_owner_run_due_emails()` iterates the SAME dict. Do NOT add a separate `add_job` — it's automatic.

**Why / dedup:** every fire calls `_owner_claim_slot(kind, slot)` → INSERT into `owner_email_log` with UNIQUE (kind, slot, sent_date) ON CONFLICT DO NOTHING. Because the unique key includes `kind`, two different kinds CAN share the same clock slot without colliding (e.g. microcap & smart_money both at 09:50). Real-time scheduler and catch-up therefore can never double-send.

**Gotcha:** the slot is claimed BEFORE the send runs, so a scan failure or empty result still burns that slot for the day (no retry). Owner emails are silent-when-empty by design. The EOD smart-money run (16:50, `_EOD_SMART_MONEY_SLOT`) is special-cased separately because that run also snapshots the L1-L8 track record.

**Heavy yfinance scans inside an email** must reuse the matching endpoint's single-flight lock+cache so the scheduled run can't collide with a user-triggered tab scan and amplify rate limits (e.g. the accumulation email reuses `app._nfmd_lock` / `app._nfmd_cache` from `/net-flow/multiday`, and refreshes the cache with its fresh post-close result).

**Accumulation Watch email** = the ALOY-style "staircase" detector: built on `_run_multiday_flow_scan` (cheap daily OHLCV, micro/small-cap universe), Balanced tuning streak>=5 AND consistency>=0.40, tags CONVICTION (streak>=10 & consistency>=0.65) vs BUILDING; fires daily at 16:25 ET. Daily-bar trend scans are far cheaper than the option-chain conviction score, so this signal can cover a broad universe without the Yahoo throttling that caps the options-based scans.
