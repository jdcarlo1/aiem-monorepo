---
name: Payment flow fixes
description: Critical Stripe payment bugs found and fixed during production audit — must verify these on any future deployment
---

# Payment Flow Fixes

## Bug 1 — Stripe webhook URL was pointing to dev server
**What:** Webhook was set to `https://[replit-dev-domain]/api/stripe/webhook` instead of `https://nclexai.org/api/stripe/webhook`.
**Effect:** Payments succeeded on Stripe but session never got unlocked. Customers stayed on paywall.
**Fix:** Updated via Stripe API to `https://nclexai.org/api/stripe/webhook`.
**Why:** `findOrCreateManagedWebhook` in index.ts used `REPLIT_DOMAINS` env var which returns the replit.app domain, not the custom domain.

## Bug 2 — Checkout success/cancel URLs pointed to wrong domain
**What:** `baseUrl` in stripe.ts was built from `REPLIT_DOMAINS` → resolved to `hello-world-2-joeldcarlo.replit.app`.
**Effect:** After paying, customers were redirected to the wrong site. Session verify-checkout ran on wrong domain's sessionId.
**Fix:** Changed to `process.env.SITE_URL ?? "https://nclexai.org"` in both stripe.ts and index.ts.

## Bug 3 — Webhook handler crashed before unlocking session
**What:** `sync.processWebhook()` threw `relation "stripe.accounts" does not exist` — crashed before session unlock ran.
**Effect:** Even with correct webhook URL, the session unlock code never executed.
**Fix:** Wrapped `sync.processWebhook()` in try-catch in webhookHandlers.ts. Session unlock always runs regardless of sync errors.

## Rule
**On every future deployment to a new domain:** verify webhook URL, success_url, and cancel_url all point to the production domain. Never rely on `REPLIT_DOMAINS` for payment URLs.

## Adaptive engine fix
First 2 questions are now single-choice cardiac questions to orient new users, then full NGN mix from question 3. Previously the engine was deliberately serving the HARDEST questions (multiple/ordered) first, driving users away.

## Bulk activate bug
Admin endpoint `/admin/activate-sessions` without a sessionId was activating ALL sessions for free. Fixed to require sessionId. 580 incorrectly-free sessions were reset in production via `/admin/fix-sessions` endpoint (now removed from codebase concern — the fix was run).
