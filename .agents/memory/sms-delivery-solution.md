---
name: SMS delivery solution
description: Email-to-SMS gateway bypasses Twilio A2P 10DLC carrier blocking for US numbers
---

# SMS Delivery: Email-to-SMS Gateway

## The Rule
Use email-to-SMS gateway as primary method, Twilio as fallback.

**Why:** Twilio upgraded accounts sending to US 10-digit long code numbers get error 30034 (A2P 10DLC carrier blocking). Toll-free numbers bypass this but are hard to acquire on mobile. Email-to-SMS gateway works instantly with existing SMTP setup.

## How to Apply
- Recipient: +14013185787 (T-Mobile)
- Gateway address: 4013185787@tmomail.net
- Code: `sms_alerts.py` → `_send_sms_via_email()` sends to `_SMS_EMAIL_GATEWAY`
- Uses existing `send_email_raw()` from `email_alerts.py`

## Carrier Gateways Reference
- T-Mobile: number@tmomail.net
- Verizon: number@vtext.com
- AT&T: number@txt.att.net
- Sprint: number@messaging.sprintpcs.com
