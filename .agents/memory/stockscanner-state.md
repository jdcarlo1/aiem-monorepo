---
name: StockScanner AI state
description: Full state of the StockScanner AI product — landing page, Stripe, SMS, key files, architecture
---

## Architecture
- React+Vite frontend: `artifacts/stock-scanner/` — preview at `/stock-scanner/`
- Python Flask API: `artifacts/stock-scanner-api/main.py` — port 5050, workflow: "artifacts/stock-scanner: stock-api"
- Node.js API server: `artifacts/api-server/` — port 8080, handles Stripe checkout + webhooks
- APScheduler scans at 9:00, 9:45, 3:30, 4:15 ET every trading day

## Stripe
- Product: `prod_UeHHtrYgG9d7Lp` / Price: `price_1TeyjGChn3bmMDTv9yXybfDR` ($29/mo)
- `STRIPE_SECRET_KEY` env secret is set
- Checkout route: `artifacts/api-server/src/routes/stripe.ts`
- Webhook handler: `artifacts/api-server/src/webhookHandlers.ts`

## Landing Page (EmailSignupBanner)
- Lives in `artifacts/stock-scanner/src/pages/Dashboard.tsx` around line 619
- Shown on the 🏆 Smart Money tab (tab id: "smartmoney")
- Design follows NCLEX landing formula: top bar → trust badges → big headline → SMS mockup → full-width CTA → price → testimonial
- Headline: "Beat the market / before it opens." (green glow on second line)
- SMS mockup styled like an iPhone Messages app (dark iOS aesthetic)
- CTA: "Start Getting Alerts →" — full-width green button

## SMS / Twilio (PENDING)
- User has not yet signed up for Twilio
- User phone: +14013185787 (for testing)
- Needs: Account SID, Auth Token, Twilio phone number as env secrets
- `email_alerts.py` has SMS stub ready — just needs credentials wired in

## Email alerts
- `artifacts/stock-scanner-api/email_alerts.py` — full email digest + high premium flow section
- SMTP not configured — needs `SMTP_USER` + `SMTP_PASS` secrets
- Subscriber id=2 is joeldcarlo@gmail.com

## Smart Money / Options logic
- `artifacts/stock-scanner-api/smart_money.py` — strike filter: 80%–150% of spot, skips 0DTE
- `artifacts/stock-scanner-api/email_alerts.py` — $5M+ premium threshold = 5000 in $K units
- 0DTE strikes filtered from both email and Dashboard options chain panel

## Key design decisions
- Default tab on load is "lookup" (Stock Lookup) — Smart Money tab requires user to click
- `top_prem_value_k` is in $K units in the options_summary response
- FREE_LIMIT on NCLEX is 10 — never change

**Why:** User asked to preserve all work across sessions so nothing is lost between conversations.
