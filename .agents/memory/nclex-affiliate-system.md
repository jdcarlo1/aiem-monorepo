---
name: NCLEX affiliate/referral system
description: How the affiliate payout logic is structured — important for avoiding double-pays
---

# NCLEX Affiliate System

## Payment split rule (critical)
- `checkout.session.completed` with `mode="payment"` → handles **lifetime only** → sends 50% transfer
- `invoice.payment_succeeded` → handles **all monthly** (first month + renewals) → sends 50% transfer
- `checkout.session.completed` with `mode="subscription"` → **no transfer** (monthly handled via invoice)

**Why:** For monthly subscriptions BOTH events fire on the first payment. Handling only one prevents double-paying.

## Key files
- `artifacts/api-server/src/routes/affiliates.ts` — admin CRUD + Stripe Connect account creation
- `artifacts/api-server/src/webhookHandlers.ts` — `sendAffiliateTransfer()` helper + payout logic
- `artifacts/api-server/src/routes/stripe.ts` — referralCode validated + stored on session at checkout
- `artifacts/nclex-prep/src/pages/admin-affiliates.tsx` — admin UI at /admin/affiliates
- `artifacts/nclex-prep/src/pages/paywall.tsx` — referral code input always visible + ?ref=CODE URL param auto-fill

## Admin access
URL: /admin/affiliates
Auth: password typed by admin at login; validated server-side against ADMIN_TOKEN env secret (never hardcoded)

## Stripe Connect
- Express accounts (fastest onboarding)
- Transfer only fires if `account.payouts_enabled === true`
- Onboarding link expires in 24h; use the "Refresh link" button to generate a new one

## DB
- `affiliates` table: code (UNIQUE), name, stripe_connect_id, commission_pct, created_at
- `sessions.referral_code` column: stored at checkout time so renewal webhooks can find it by customer_id

## DB migration
drizzle-kit push requires interactive TTY — use raw SQL via executeSql() instead.
