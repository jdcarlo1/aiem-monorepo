---
name: SMS delivery solution
description: Email-to-SMS gateway bypasses Twilio A2P 10DLC carrier blocking for US numbers
---

# SMS Delivery: Email-to-SMS Gateway

## The Rule
Use ntfy.sh push notification as primary method, email gateway as secondary, Twilio as last resort.

**Why:** Twilio error 30034 (A2P 10DLC carrier blocking) permanently blocks regular SMS to T-Mobile numbers. Email-to-SMS gateway (tmomail.net) works but gets rate-throttled if >3 messages sent in quick succession (resets overnight). ntfy.sh bypasses all carrier filtering entirely.

## Primary: ntfy.sh Push Notification
- Topic: `stockscanner-joel-9x7k2`
- API: POST to `https://ntfy.sh/stockscanner-joel-9x7k2`
- Headers: Title (ASCII only — no emoji), Priority (urgent/high), Tags
- Body: plain text, UTF-8 encoded
- User has ntfy app installed and subscribed to topic — CONFIRMED WORKING
- **Important:** Do NOT put emoji in the Title header — causes latin-1 UnicodeEncodeError. Emoji are fine in body/Tags.

## Secondary: Email-to-SMS Gateway
- Recipient: +14013185787 (T-Mobile, confirmed)
- Gateway address: 4013185787@tmomail.net
- Works for single daily message; throttled after burst sends (resets overnight)
- msg.t-mobile.com is INVALID — do not use

## Last Resort: Twilio
- Permanently blocked (error 30034) for 10-digit long codes to T-Mobile
- Toll-free number would bypass this but requires Twilio setup

## Carrier Gateways Reference
- T-Mobile: number@tmomail.net
- Verizon: number@vtext.com
- AT&T: number@txt.att.net
- Sprint: number@messaging.sprintpcs.com
