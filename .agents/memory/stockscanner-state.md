---
name: StockScanner AI state
description: Full state of the StockScanner AI product — landing page, Stripe, SMS, key files, architecture
---

## Architecture
- React+Vite frontend: `artifacts/stock-scanner/` — preview at `/stock-scanner/`
- Python Flask API: `artifacts/stock-scanner-api/main.py` — port 5050, workflow: "artifacts/stock-scanner: stock-api"
- Node.js API server: `artifacts/api-server/` — port 8080, handles Stripe checkout + webhooks
- APScheduler scans at 9:00, 9:45, 3:30, 4:15 ET every trading day
- Signal snapshot job at 4:00 PM ET Mon-Fri (saves to signal_history table for persistence tracking)
- Outcome tracker job at 4:30 PM ET (T+3/5/10 day price outcomes)

## AI Trades — 18+ Signal Types, 11 Sources
The `/stock-api/ai-trades` route aggregates all of the below into a GPT prompt:
1. Composite Score Board
2. Vol Crush + Price Structure (RSI, SMA50%, vol_trend_5d, net_upgrades_7d, days_since_earnings)
3. Call Intent Decoder (call_vol_oi = volume/OI ratio per strike)
4. Put Intent / Bear Flow
5. Smart vs Retail divergence
6. Max Pain / Pinning
7. Gamma Wall
8. Dark Pool Flow
9. Live Signal Feed
10. Pre-market Movers
11. Sector / Index Momentum
Plus: macro calendar, implied move, short interest, earnings proximity

## 5 New Indicators (built June 2026)
- **Multi-day persistence**: `signal_history` DB table snapshots daily at 4 PM; ai_trades queries for consecutive days above threshold → shows `persist=3d` to GPT
- **Call vol/OI ratio**: `call_vol_oi` from `_ci_cache`, shown as `vol/oi=3.2x` — filters concentrated new positions from retail churn
- **Analyst revision velocity**: `net_upgrades_7d` from `tkr.upgrades_downgrades` in vol-crush `_analyze()`
- **Post-earnings IV crush timing**: `days_since_earnings` — if 0-10 days past earnings, shows `post_earnings=Nd_ago(IV_crush_window)` to steer GPT toward credit spreads
- **Price structure**: `rsi` (14-period), `sma50_pct` (% vs 50d SMA), `vol_trend_5d` (5d/20d avg vol ratio) — all in vol-crush `_analyze()`

## GPT Priority Weighting (system prompt)
1. persist=3d+ (multi-day confirmation — rarest, most reliable)
2. Smart vs Retail divergence (institutional vs retail misalignment)
3. composite score ≥75 + vol_trend surging
4. call vol/oi >2x (unusual concentrated activity)
5. post-earnings IV crush window
6. analyst upgrades + premarket gap
7. dark pool + earnings catalyst
8. IV rank extremes + macro timing

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
- Design: black BG, orange #FF6600 accents, IBM Plex Mono font, 3-column layout
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
- `anthropic` Python package installed via `pip install anthropic --user` (nix store is read-only; --user flag required)
- AI Analysis sub-tab in Stock Lookup shows: orange "CLAUDE AI" badge, "↻ REFRESH" button, text panel, score/ML below

## DB Tables (PostgreSQL)
- `ai_trade_log` — every GPT trade pick stored for track record
- `signal_history` — daily signal snapshots (ticker, signal_date, comp_score, smart_cp, call_verdict, dp_prem_m, iv_rank)
- `signal_outcomes` — T+3/5/10 price outcomes for win rate tracking
- `daily_top10`, `answers`, `questions`, `score_history`, `sessions`, `sm_subscribers`

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
- BB_BG="#060c14", BB_PANEL="#0b1320", accent green="#22c55e", monospace terminal font

**Why:** User asked to preserve all work across sessions so nothing is lost between conversations.
