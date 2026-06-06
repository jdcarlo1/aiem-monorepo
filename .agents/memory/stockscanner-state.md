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

## AI Trades — 50+ Data Points, 17 Sources
The `/stock-api/ai-trades` route aggregates all of the below into a GPT prompt:
1. Composite Score Board
2. Vol Crush + Price Structure (RSI, SMA50%, vol_trend_5d, net_upgrades_7d, days_since_earnings, options_liquidity_pct, earnings_beat_streak, spy_beta)
3. Call Intent Decoder (call_vol_oi = volume/OI ratio per strike)
4. Put Intent / Bear Flow
5. Smart vs Retail divergence
6. Max Pain / Pinning
7. Gamma Wall
8. Dark Pool Flow
9. Live Signal Feed
10. Pre-market Movers
11. Sector / Index Momentum
12. Multi-day Signal Persistence (signal_history table, 4 PM snapshots)
13. Options Liquidity Filter (bid/ask spread %)
14. Market Regime Detection (VIX + SPY 5d/20d)
15. Self-learning win rates (ai_trade_log)
16. Historical Win Rates
17. Macro Cross-Asset (yield curve, USD, credit spreads, crude, gold)
Plus: macro calendar, implied move, short interest, earnings proximity

## 7 New Quant Hedge-Fund Signals (built June 2026)
All computed in vol-crush `_analyze()` — 100% populated in production:
- **iv_skew**: OTM put IV minus OTM call IV at ~25-delta equiv. Positive = fear premium / downside hedging active. FEAR_PREMIUM>8pp = institutional crash protection.
- **iv_term_structure**: Near-term ATM IV minus next-expiry ATM IV. BACKWARDATION>5pp = event/earnings risk priced in near term.
- **gex_m / gex_regime**: Dealer Gamma Exposure in $M via Black-Scholes gamma approx across full options chain. LONG_GAMMA = suppressive/mean-revert; SHORT_GAMMA = amplifying/directional.
- **iv_rv_premium**: (IV/HV ratio - 1) × 100. RICH_SELL_PREM>20% = edge selling premium; CHEAP_BUY_VOL<-10% = edge buying vol.
- **momentum_12_1**: Price return from oldest bar to 21 bars ago (Fama-French 12-1 month factor). >15% = strong momentum; <-15% = weak.
- **factor_roe / factor_fpe**: ROE from tkr.info["returnOnEquity"] (quality factor); forward P/E from tkr.info["forwardPE"] (value factor). CHEAP<15x, EXPENSIVE>35x.
- **sector_corr**: 30-day correlation to sector ETF (XLK/XLF/XLV/XLE/XLI/XLC/XLY). IDIOSYNCRATIC<0.5 = name-specific catalyst, preferred.
- **news_sentiment**: Keyword-based scoring of recent tkr.news headlines (±word matching). Fast, no API cost.
- **Macro Cross-Asset** (section 17 in ai_trades): yield curve (^TNX - ^IRX), USD via UUP (NOT ^DXY — delisted), HYG vs LQD credit spread, crude (USO), gold (GLD).

## Implementation details (quant signals)
- Sector ETF pre-fetch: happens before ThreadPoolExecutor in vol-crush route — dict `_TICKER_TO_SECTOR_ETF` + `_sector_rets_map` accessed via closure
- Use `UUP` (not `^DXY` — delisted on yfinance) for USD strength in macro cross-asset
- GEX uses numpy normal PDF (np.exp(-0.5*d1**2)/np.sqrt(2*pi)) — no scipy dependency
- momentum_12_1 threshold: `len(hist) >= 60` (not 252) — uses `hist.iloc[0]` as 12m reference
- sig_lines limited to top 10 tickers (was 15) + max_completion_tokens=6000 (was 4000) to avoid GPT truncation
- All 7 new fields wired into ai_trades section 2 (vol-crush wiring block)
- GPT system prompt updated with 13 rules covering all new signals

## GPT Priority Weighting (updated system prompt)
1. opt_spread>12% → SKIP (liquidity gate)
2. MARKET_REGIME + MACRO_CROSS_ASSET → valid setup_types
3. GEX regime → LONG=mean-revert; SHORT=directional
4. Historical win rates → self-learning bias
5. persist=3d+ (multi-day confirmation)
6. Smart vs Retail divergence
7. iv_rv + iv_skew (vol surface edge)
8. score≥75 + vol_trend + beta + momentum
9. call vol/oi >2x (unusual activity)
10. post-earnings + earn_beat + ROE + fwd_PE
11. sector_corr=IDIOSYNCRATIC
12. analyst upgrades + premarket + news

## Stripe
- Product: "StockScanner AI Pro" — Price: `price_1TfQfiChn3bmMDTvww8LpUIn` ($59/mo, ACTIVE)
- OLD price `price_1Tf9Q7Chn3bmMDTvtCrhUJud` ($39/mo) — DEACTIVATED, do not use
- OLD price `price_1TeyjGChn3bmMDTv9yXybfDR` ($29/mo) — DEACTIVATED, do not use
- Checkout route dynamically looks up active price for "StockScanner AI Pro" product
- `STRIPE_SECRET_KEY` env secret is set
- Checkout route: `artifacts/api-server/src/routes/stripe.ts` at `/stock-scanner/checkout`
- Webhook handler: `artifacts/api-server/src/webhookHandlers.ts`

## Landing Page
- `artifacts/stock-scanner/src/pages/Landing.tsx` — "16 scanners. One AI thesis."
- $59/mo price, crossed-out $79, "🔥 Limited Time — Price goes up soon" badge, "save $20/mo"
- 5 gold highlighted checklist items: AI Synthesis, Put Intent, Dark Pool, Market Regime, Self-Learning AI
- 2 additional plain checklist items: Multi-Day Persistence, Options Liquidity Filter
- Features grid: 20 cards total including 4 new quant cards

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
