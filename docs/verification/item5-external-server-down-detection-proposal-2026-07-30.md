# Item 5 — External Whole-Server-Down Detection: Proposal
**Date:** 2026-07-30T21:30Z UTC / 2026-07-30 17:30 ET
**Directive:** Open Items 3–7 Closeout, 2026-07-30
**Status:** PROPOSAL ONLY — no implementation

---

## Problem Statement

The Jul 27 premarket outage (6:50–9:37 AM ET) was invisible until after the trading window.
The internal watchdog (aiem-process watchdog in the notifier) and GH Actions crons both
failed to detect and alert on the outage because:

1. Internal watchdogs require at least one of the three services (stock-api, aiem-process,
   aiem-telegram) to be alive to send a Telegram message. When all three are down, no internal
   alert can be sent.
2. GH Actions cron silently skipped its scheduled runs on Jul 27 (known GH infrastructure
   limitation). Even when it fires, it cannot restart a crashed Replit VM.

What is needed: a mechanism that fires reliably from **outside the Replit VM** whenever the
entire stack is unresponsive, without depending on any of the three internal processes.

---

## Proposal

### Recommended: UptimeRobot Free Tier + Dedicated Health Endpoint

**Service:** UptimeRobot (https://uptimerobot.com) — free tier supports 50 monitors at 5-minute
polling intervals. Paid plans offer 1-minute polling.

**How it works:**
1. A dedicated lightweight health endpoint is exposed on the stock-api process (already exists:
   `GET /stock-api/health` returns HTTP 200 with JSON). UptimeRobot polls this URL every 1–5
   minutes from its own infrastructure (multiple global nodes).
2. If the endpoint returns non-2xx or times out for two consecutive checks (2–10 minutes total),
   UptimeRobot sends an alert.
3. Alert delivery: email + optionally webhook. The webhook can call a Telegram Bot API URL
   directly (no Replit process needed) to post an alert to the AIEM_StockScanner_bot channel.

**Advantages:**
- Completely external — fires even when Replit VM is fully down
- Free tier: 5-minute polling, email alert. $7/month plan: 1-minute polling, webhook support
- Zero code on the Replit side (endpoint already exists)
- No GH Actions reliability dependency

**Configuration required:**
- Create UptimeRobot account (free)
- Add monitor: `HTTP(S)` type, URL = `https://<REPLIT_APP_URL>/stock-api/health`,
  check interval = 5 minutes (free) or 1 minute (paid)
- Alert contact: email (free) or webhook pointing to
  `https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=8609255707&text=🚨+STOCK-API+DOWN`
  (paid webhook — or use a free Zapier/Make.com webhook as an intermediary)

**Rough cost:** $0/month (5-min polling, email only) or $7/month (1-min polling, webhook)

**Rough complexity:** ~30 minutes to set up. No code changes to the Replit project.

---

### Alternative A: GH Actions Self-Hosted Cron with Telegram Direct Call

**How it works:**
- A GH Actions workflow fires every 5 minutes (cron `*/5 * * * *`). If the health endpoint
  returns non-2xx, the workflow calls the Telegram Bot API directly from the GH runner.
- This does NOT require stock-api to be running (Telegram call is from GH runner).

**Disadvantage:** GH Actions scheduled crons are unreliable — confirmed to silently skip days
(see Jul 27 evidence). This is the same mechanism that failed in the Jul 27 outage.

**Verdict:** Not recommended as sole mechanism. Acceptable as a redundant second layer.

---

### Alternative B: Dedicated External VM (VPS / Fly.io / Railway)

**How it works:**
- A tiny always-on process (e.g., 10-line Python script) runs on a separate cloud provider
  (Fly.io free tier, Railway, DigitalOcean $4/month). It polls the Replit health endpoint every
  minute and calls Telegram API directly on failure.

**Advantages:** Most reliable — no dependency on GitHub infra or Replit VM.
**Disadvantage:** Requires a second deployment to maintain. Adds operational surface area.

**Rough cost:** $0–$4/month (Fly.io free tier)

---

### Alternative C: Freshness-Based DB Sentinel

**How it works:**
- The stock-api process writes a "I am alive" row to an external Postgres/Redis/Supabase DB
  every 5 minutes. A separate UptimeRobot-style service checks the external DB for a recent
  row. If no row in 10 minutes → alert.

**Disadvantage:** Adds an external DB dependency. Row write can fail independently of the health
endpoint. More complex than a simple HTTP poll.

**Verdict:** Over-engineered for this use case.

---

## Recommended Next Action

1. **Immediate:** Set up UptimeRobot free tier (5-min polling, email alert) — 30 minutes, $0.
2. **If 1-minute polling is desired:** Upgrade to UptimeRobot $7/month and add Telegram
   webhook for direct channel notification.
3. **Redundant layer (optional):** Keep GH Actions cron as a secondary alert, with the
   understanding that it may skip and is not the primary mechanism.

The existing `/stock-api/health` endpoint (returns `{"status":"ok", "uptime_s":...,
"last_checkpoint_ts":...}`) is sufficient for the UptimeRobot health check with no code
changes required.

---

## Out of Scope

This proposal does not cover:
- Auto-restart of the Replit VM (not possible from external services; Replit platform's own
  crash-restart mechanism is the only path)
- Detection of partial failures (e.g., stock-api running but aiem-process down) — those are
  covered by the internal watchdog in aiem_telegram_notifier.py
