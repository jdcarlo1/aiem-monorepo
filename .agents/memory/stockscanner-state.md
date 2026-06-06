---
name: StockScanner AI state
description: Full state of the StockScanner AI product — landing page, Stripe, SMS, key files, architecture
---

## Architecture
- React+Vite frontend: `artifacts/stock-scanner/` — preview at `/stock-scanner/`
- Python Flask API: `artifacts/stock-scanner-api/main.py` — port 5050, workflow: "artifacts/stock-scanner: stock-api"
- Node.js API server: `artifacts/api-server/` — port 8080, handles Stripe checkout + webhooks
- APScheduler: 9:00, 9:05, 9:45, 3:30, 4:00, 4:05, 4:15, 4:30 ET every trading day
- Signal snapshot job at 4:00 PM ET (saves to signal_history table)
- Daily vol snapshot job at 4:05 PM ET (saves to daily_vol_snapshots table — builds IV skew + short float percentile history)
- SPY cache refresh at 9:05 AM ET (module-level _spy_1y_cache dict)
- Outcome tracker at 4:30 PM ET (T+3/5/10 day price outcomes)

## AI Trades — 60+ Data Points, 32 Rules, 5 Setups
The `/stock-api/ai-trades` route aggregates all scanners into a GPT-5-mini prompt.
**Output: exactly 5 trade setups** (changed from 3) — sorted by conviction, aim 2-3 BULLISH / 1 NEUTRAL / 1-2 BEARISH.
max_completion_tokens=9000 (bumped from 6000 to handle 5 setups).

## Signal Stack — 99% of free-data buildable (as of June 2026)
All computed in vol-crush `_analyze()` (Q1-Q18):

### Vol surface (Q1-Q4)
- iv_skew, iv_term_structure, gex_m/gex_regime, iv_rv_premium

### Price/momentum (Q5-Q6)
- momentum_12_1, sector_corr, spy_beta

### Short interest (Q7-Q8)
- short_float_pct, short_ratio, borrow_cost_proxy (Q8: HIGH_BORROW≥20%, ELEVATED_BORROW≥10%)

### Earnings/fundamental (Q9, Q10, Q11, Q13)
- earnings_impl_move_pct, eps_revision_trend, hist_earn_reaction_pct
- analyst_dispersion_pct (Q13: HIGH_DISAGREEMENT≥30% → prefer straddle)

### Options flow (Q7 derivatives, Q12)
- call_vol_oi_ratio, put_vol_oi_ratio (flow persistence: STRUCTURAL<0.05 vs FRESH>0.25)
- pc_premium_ratio (dollar-weighted put/call spend ratio)
- week52_range_pct, squeeze_risk (composite: 4-factor score)

### NEW signals batch (Q14-Q18, built June 2026)
- rs_vs_spy: stock 1y return minus SPY 1y return (BEATING_MARKET>+20%)
- money_flow_ratio: up-day vol / down-day vol (ACCUMULATION>1.3, DISTRIBUTION<0.8)
- insider_net: net insider buy/sell last 30d — uses Text column + Start Date column from yfinance insider_transactions
- div_yield_pct + ex_div_days: dividend yield % (normalized: >1 means already pct) + days to ex-dividend
- tail_risk_put_pct: % of put vol in deep OTM strikes >15% below spot (CRASH_HEDGING>40%)

### Future percentile signals (activate automatically after 30+ daily snapshots)
- iv_skew_pctl: today's skew ranked vs 1-year history (stored in daily_vol_snapshots table)
- short_float_trend: short float change vs 5 sessions ago

## SPY Cache (module-level)
- `_spy_1y_cache = {"return_pct": None, "rets_arr": None, "date": None}`
- `_refresh_spy_1y_cache()` called at startup + 9:05 AM ET daily
- vol_crush() reads from cache (NOT per-request download) to avoid rate-limit collisions with 20 concurrent ticker fetches

## DB Tables (PostgreSQL)
- `ai_trade_log` — every GPT trade pick stored for track record
- `signal_history` — daily signal snapshots (ticker, signal_date, comp_score, smart_cp, call_verdict, dp_prem_m, iv_rank)
- `signal_outcomes` — T+3/5/10 price outcomes for win rate tracking
- `daily_vol_snapshots` — daily iv_skew, short_float, pc_oi_ratio, pc_prem_ratio, rs_vs_spy per ticker
- `daily_top10`, `answers`, `questions`, `score_history`, `sessions`, `sm_subscribers`

## Stripe
- Product: "StockScanner AI Pro" — Price: `price_1TfQfiChn3bmMDTvww8LpUIn` ($59/mo, ACTIVE)
- OLD prices deactivated: $39/mo and $29/mo — do not use
- Checkout route dynamically looks up active price for "StockScanner AI Pro" product
- `STRIPE_SECRET_KEY` env secret is set
- Checkout route: `artifacts/api-server/src/routes/stripe.ts` at `/stock-scanner/checkout`
- Webhook handler: `artifacts/api-server/src/webhookHandlers.ts`

## Landing Page
- `artifacts/stock-scanner/src/pages/Landing.tsx` — "16 scanners. One AI thesis."
- $59/mo price, crossed-out $79, "🔥 Limited Time — Price goes up soon" badge

## Signal Outcome Tracker
- `artifacts/stock-scanner-api/signal_outcomes.py` — auto-stores signals on every Bull Flow scan
- `/stock-api/outcomes` endpoint — T+3/5/10 day price outcomes tracked

## Smart Money / Options logic
- `artifacts/stock-scanner-api/smart_money.py` — strike filter: 80%–150% of spot, skips 0DTE
- DEFAULT_LEADERBOARD = 50 tickers for all scans
- Use `UUP` (not `^DXY` — delisted on yfinance) for USD strength
- GEX uses numpy normal PDF — no scipy dependency
- FREE_LIMIT on NCLEX is 10 — never change

## SMS / Twilio (PENDING)
- User has not yet signed up for Twilio
- User phone: +14013185787 (for testing)
- `email_alerts.py` has SMS stub ready — just needs credentials

## Email alerts
- `artifacts/stock-scanner-api/email_alerts.py` — full email digest
- SMTP not configured — needs `SMTP_USER` + `SMTP_PASS` secrets
- Subscriber id=2 is joeldcarlo@gmail.com

## Key design decisions
- Default tab on load is "lookup" (Stock Lookup)
- `top_prem_value_k` is in $K units in the options_summary response
- BB_BG="#060c14", BB_PANEL="#0b1320", accent green="#22c55e", monospace terminal font

**Why:** User asked to preserve all work across sessions so nothing is lost between conversations.
