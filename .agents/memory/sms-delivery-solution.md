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

## The trap that hid the breakage (important)
The SMS path was once "confirmed working" — but that was a **manual one-off test
only**. Scheduled production alerts were NEVER delivering, because the morning and
exit scan functions opened with a delivery-channel availability gate
(`if not sms_configured(): return`, a Twilio env-var check). When Twilio wasn't
configured/healthy the scan bailed *before* sending anything — not even the backup
email. 

**Lesson:** never gate scan/compute logic on the availability of a *specific*
delivery channel. Gate on whether the channel you actually use is configured
(here: `smtp_configured()`), or don't gate at all and let the sender no-op. A
channel-availability check sitting in front of the work means switching channels
silently disables the whole feature until you also move the gate.

## Still true
- ntfy.sh push: topic `stockscanner-joel-9x7k2`; do NOT put emoji in the ntfy
  Title header (latin-1 UnicodeEncodeError) — emoji are fine in body/Tags.
- Owner email recipient defaults to the owner inbox; overridable via `ALERT_EMAIL`.
- SMTP sends now carry a 20s timeout so a hung connection can't freeze a scan.

## Deployment caveat
Code changes only reach production on republish (owner must do it from a computer),
and the scheduler only fires reliably on an always-on Reserved VM — so this
email switch is inert in prod until the owner republishes as Reserved VM.
