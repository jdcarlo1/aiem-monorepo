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
- Product: "StockScanner AI Pro" — Price: `price_1Tf9Q7Chn3bmMDTvtCrhUJud` ($39/mo, ACTIVE)
- OLD price `price_1TeyjGChn3bmMDTv9yXybfDR` ($29/mo) — DEACTIVATED, do not use
- Checkout route dynamically looks up active price for "StockScanner AI Pro" product
- `STRIPE_SECRET_KEY` env secret is set
- Checkout route: `artifacts/api-server/src/routes/stripe.ts` at `/stock-scanner/checkout`
- Webhook handler: `artifacts/api-server/src/webhookHandlers.ts`

## Landing Page
- `artifacts/stock-scanner/src/pages/Landing.tsx` — $39/mo price, crossed-out $59, "🔥 Limited Time Offer" badge, "save $20/mo"
- Dashboard landing banner: `artifacts/stock-scanner/src/pages/Dashboard.tsx` around line 619 (Smart Money tab)

## Bloomberg Terminal Mockup (CANVAS — not yet graduated to main app)
- Component: `artifacts/mockup-sandbox/src/components/mockups/bloomberg-terminal/Dashboard.tsx`
- Canvas shape ID: `bloomberg-dashboard` — state: "live"
- Preview URL: `https://6536a28a-761f-478a-b95d-a95c18a9d21e-00-14lah2h4q073y.janeway.replit.dev/__mockup/preview/bloomberg-terminal/Dashboard`
- Design: black BG, orange #FF6600 accents, IBM Plex Mono font, 3-column layout
  - Left: Today's Top 10 leaderboard with score bars
  - Center top: Bull Flow signals table (C/P ratio, premium $M, strike, expiry, bull/bear)
  - Center bottom: 8-sector heatmap with strength bars
  - Right: Live alerts feed + market indices
  - Bottom: Scrolling live ticker tape (green/red color-coded)
  - Top: Nav tabs + live clock + market quotes bar
- NEXT STEP: User says "graduate it" → use mockup-graduate skill to apply to main Dashboard.tsx

## Signal Outcome Tracker (BUILT)
- `artifacts/stock-scanner-api/signal_outcomes.py` — PostgreSQL table, auto-stores signals on every Bull Flow scan
- `/stock-api/outcomes` endpoint — T+3/5/10 day price outcomes tracked
- `OutcomesTab` component in `artifacts/stock-scanner/src/pages/Dashboard.tsx`

## Daily Top 10 Banner (BUILT)
- `/stock-api/daily-top10` endpoint — daily cache, returns top 10 from Bull Flow
- `DailyTop10Banner` component — auto-loads at top of Scanner and Analytics tabs with click-to-analyze

## Claude AI Swing Analysis (BUILT)
- `/stock-api/ai-analyze` POST endpoint in `main.py` — calls Anthropic API server-side via Replit AI Integration
- Replit integration env vars: `AI_INTEGRATIONS_ANTHROPIC_BASE_URL` + `AI_INTEGRATIONS_ANTHROPIC_API_KEY` (provisioned)
- `anthropic` Python package installed via `pip install anthropic --user` (nix store is read-only; --user flag required)
- `fetchAIAnalysis()` function in `artifacts/stock-scanner/src/lib/api.ts`
- AI Analysis sub-tab in Stock Lookup shows: orange "CLAUDE AI" badge, "↻ REFRESH" button, text panel, score/ML below
- State: `aiText`, `aiLoading`, `aiError`, `aiTicker` — only shows analysis when `aiTicker === analysis.ticker`

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
