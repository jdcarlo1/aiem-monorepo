---
name: Alert delivery channel
description: Personal alerts are EMAIL-ONLY; Twilio + carrier SMS gateway fully removed and why
---

# Alert Delivery: Email-Only

## The Rule
Personal owner alerts (morning movers, exits, profit targets, midday/gap/grinder,
gamma/insider/dual signals, EOD conviction, pre-close swings) are delivered ONLY by
email (SMTP via `email_alerts.send_email_raw`). Twilio REST and the carrier
email-to-text gateway (`*@tmomail.net`) have been fully stripped out. ntfy.sh push
still exists as an optional extra channel.

**Why:** Twilio A2P 10DLC (error 30034) permanently blocks SMS to the owner's
T-Mobile number, and the tmomail gateway is unreliable/rate-throttled and risks
getting the Gmail sending account flagged. Email is the one channel that reliably
lands.

## What was actually happening in prod (corrected)
The owner HAS been receiving alerts as **backup emails** every morning. The old
live `send_sms()` tried the carrier email-to-text gateway (`*@tmomail.net`) first,
and only when that gateway dropped the message did it fall back to emailing the
owner's Gmail. Because the carrier keeps dropping the text, the backup email fires —
so the emails landing in the inbox ARE the real alerts working via fallback. Do NOT
claim alerts "went silent" or "never delivered"; that was an incorrect diagnosis.
(Twilio IS configured in prod, so `sms_configured()` returned True and the scans did
NOT bail.)

The new email-only version removes the texting attempt entirely so it emails the
owner directly every time — no carrier-gateway step that drops messages or risks
flagging the Gmail sender.

**Design lesson that still holds:** don't gate scan/compute logic on a *different*
channel than the one you actually send through. The old scans gated on
`sms_configured()` (Twilio) while delivery happened via the email gateway+backup —
a latent trap (removing Twilio would have bailed the scan even though email works).
Gate on the channel you use (`smtp_configured()`) or don't gate at all.

## Still true
- ntfy.sh push: topic `stockscanner-joel-9x7k2`; do NOT put emoji in the ntfy
  Title header (latin-1 UnicodeEncodeError) — emoji are fine in body/Tags.
- Owner email recipient defaults to the owner inbox; overridable via `ALERT_EMAIL`.
- SMTP sends now carry a 20s timeout so a hung connection can't freeze a scan.

## Deployment caveat
Code changes only reach production on republish (owner must do it from a computer),
and the scheduler only fires reliably on an always-on Reserved VM — so this
email switch is inert in prod until the owner republishes as Reserved VM.
