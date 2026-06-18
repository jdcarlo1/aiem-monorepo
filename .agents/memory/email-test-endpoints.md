---
name: Email test endpoints & recipient safety
description: Which prod endpoints send test/alert emails and who they reach — avoid blasting subscribers when testing
---

# Sending a test/owner email in production

**Verified live:** `GET /stock-api/alerts/count` → `{"smtp_configured":true,"subscribers":N}`. Use it to confirm SMTP + how many people a send would reach BEFORE triggering any email.

## Recipient behavior (the trap)
- `POST /stock-api/alerts/test-digest?session=morning|eod` → calls `send_daily_digest`, which loops `get_active_subscribers()` and emails **every active subscriber**. Safe as a self-test ONLY while the owner is the sole subscriber.
- `POST /stock-api/admin/test-emails` → fires all six daily emails. It calls `_send_microcap_calls_email()` and `_send_high_conviction_email()` **without** `owner_only=True`, and `owner_only` defaults to `False` → those two **blast all active subscribers**. Do NOT use this as an "owner-only" test.

**Why it matters:** a test must never spam paying subscribers. Today the owner is the only subscriber so both endpoints are safe; the moment real subscribers exist, both will email everyone.

## How to send owner-only once real subscribers exist
- `owner_only=True` paths exist (`_send_microcap_calls_email(owner_only=True)`, `_send_high_conviction_email(owner_only=True)`) but are only called by the scheduler — there is **no HTTP endpoint** that triggers them owner-only.
- For an owner-only test, use the `send_email_raw(_OWNER_EMAIL, ...)` path, or add an `owner_only`/`to` param to a test endpoint first. `_OWNER_EMAIL = os.getenv("ALERT_EMAIL", "joeldcarlo@gmail.com")`.

## Operational gotcha
`test-digest` runs a live `scan_smart_money` first, so it is slow (10–90s, worse under yfinance rate-limiting). The HTTP client (curl/sandbox fetch) may time out while the **server still completes and returns 200** — check deployment logs for the `POST .../test-digest ... 200` line rather than trusting the client.
