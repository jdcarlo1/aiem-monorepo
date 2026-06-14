---
name: StockScanner AI state
description: Full state of the StockScanner AI product — landing page, Stripe, SMS, key files, architecture
---

## Architecture
- React+Vite frontend: `artifacts/stock-scanner/` — preview at `/stock-scanner/`
- Python Flask API: `artifacts/stock-scanner-api/main.py` — port 5050, workflow: "artifacts/stock-scanner: stock-api"
- Node.js API server: `artifacts/api-server/` — port 8080, handles Stripe checkout + webhooks
- APScheduler: 9:00, 9:05, 9:45, 3:30, 4:00, 4:05, 4:15, 4:30 ET every trading day
- Morning inflows scans: 9:31, 9:33, 9:35, 9:38, 9:41, 9:45, 10:00, 10:15, 10:30 AM ET (tightened June 2026 for hedge-fund timing)
- News Catalyst scans: same tight window 9:31–10:30 AM ET (parallel track, separate SMS label)
- Email schedule (Mon-Fri ET): morning_inflows 9:46/10:01/10:16/10:31 AM | eod_accum 3:46 PM | ai_trades 10:00 AM | unusual_calls 9:47 AM + 3:15 PM | microcap_calls 10:32 AM + 3:16 PM | high_conviction 9:48 AM + 3:17 PM
- Position monitor: `position_monitor` DB table; email TRADE: BUY MSFT 420c 6/20 to yourself to log; IMAP poller every 15 min; exit signal checker every 30 min; fires at score ≥3 (put flow +2, call disappear +2, MACD cross +1, RSI≥75 +1, weak close +1)
- Signal snapshot job at 4:00 PM ET (saves to signal_history table)
- Daily vol snapshot job at 4:05 PM ET (saves to daily_vol_snapshots table — builds IV skew + short float percentile history)
- EOD accum scans: 3:45 PM and 3:55 PM ET — saves to eod_accum_picks table
- SPY cache refresh at 9:05 AM ET (module-level _spy_1y_cache dict)
- Outcome tracker at 4:30 PM ET (T+3/5/10 day price outcomes)

## ICS Scoring System (as of June 2026)
- Total weight: 200 pts (120 original + 80 holy grail)
- SMS threshold: 80+ → text fires
- Score = pts/200 * 100. Labels: 80+=EXTREME🔥🔥🔥, 70+=HIGH⭐⭐⭐
- **Original signals (120 pts)**: RVOL 3x+(10), Above VWAP(8), Price chg 1%+(8), Gap up(7), Bid/Ask Spread Tightening(7), + 6 others from options_sweep.py
- **Holy Grail signals (80 pts)** in `holy_grail.py`: Delta Flow(10), Tape Reading(8), VWAP 2nd StdDev(8), MFI>70(8), Price Acceleration(7), Consecutive Green(6), Pre-Mkt Vol 5x(8), VWAP Reclaim(8), Minute RVOL 3x(10), Bid/Ask Spread(7)
- VWAP Reclaim also runs standalone every 5 min — texts immediately on any previously-alerted ticker

## News Catalyst Scanner (NEW — June 2026)
- File: `artifacts/stock-scanner-api/news_catalyst.py`
- Parallel to ICS, completely independent, does NOT change ICS logic
- 4 signals (100 pts total): Blowout RVOL >15x(30), News keyword(20), Recovery confirmation(25), Sustained volume 3x+(25)
- SMS threshold: 75+. Text labeled "📰 NEWS CATALYST" so user knows which scanner fired
- Targets stocks like ELVN: massive opening volume, news catalyst, choppy open then recovers
- DB table: `news_catalyst_log` (ticker, alert_date unique constraint, price, score, catalyst)
- Runs 9:31–10:30 AM ET only (same window as ICS)

## SMS Alert Flow (Monday morning)
1. 🔥 ICS Entry text (9:31-9:33 AM) → buy
2. 📰 News Catalyst text (if news gap fires) → different setup type
3. 🎯 +10% profit target text → set trailing stop, let it run
4. ⚠️ VWAP break exit text → tighten stop or exit
- SMS: primary via 4013185787@tmomail.net (T-Mobile email-to-SMS), backup joeldcarlo@gmail.com
- User phone: +14013185787 (T-Mobile)

## Backtest Findings (June 2026)
- **Catches** (ICS 80+): ZDGE (+25.68%, score 86), TXMD (+20.88%, score 91) — gap-up institutional burst at 9:31
- **Correctly ignores**: ALMS, ARM, TBN — slow all-day grinders, no opening volume signature
- **News catalyst track catches**: ELVN (+14.3%, 35x RVOL) — blowout open volume with news, choppy recovery
- **Key insight**: ICS requires CLEAN one-directional buying at open. Biotech news gaps have two-sided action → scored low on ICS but caught by news catalyst scanner
- **TBN**: 0 shares at 9:30 AM, move started 11:10 AM → neither scanner catches, by design

## Scanner Pattern Dictionary
- Gap-up institutional burst (ZDGE/TXMD type): ICS 80+ at 9:31
- Biotech news catalyst (ELVN type): News Catalyst 75+ at 9:35-9:45
- Slow all-day grinder (ALMS/ARM/TBN type): Neither scanner fires — correct behavior
- Bounce/recovery from gap-down (RFL type): Neither scanner fires — different setup

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
- `signal_history` — daily signal snapshots; has `scan_time` (timestamptz, UTC); ticker, scan_date, price_chg_pct, rel_vol, flow_ratio, standout_score
- `signal_outcomes` — T+3/5/10 price outcomes for win rate tracking
- `daily_vol_snapshots` — daily iv_skew, short_float, pc_oi_ratio, pc_prem_ratio, rs_vs_spy per ticker
- `daily_top10`, `answers`, `questions`, `score_history`, `sessions`, `sm_subscribers`
- `unusual_calls_log` — per-tick options flow; date column is `first_seen` (NOT `log_date`); populated by scheduled scans
- `morning_inflows_cache` — columns: scan_date, payload (JSON), saved_at; ONE row per day, overwritten each scan; payload has scanned/standouts/criteria/generated_at
- `eod_accum_picks` — columns: scan_date, ticker, close_price, accum_score, eod_rel_vol, closing_range, late_flow, news_headline; started June 11, 2026
- `eod_accum_outcomes` — tracks next-day outcomes of EOD accum picks
- `sms_alerts_log` — ICS SMS alerts (ticker+date unique)
- `sms_profit_log` — +10% profit target alerts (ticker+date unique)
- `news_catalyst_log` — news catalyst SMS alerts (ticker+date unique)

## Scan time → UTC conversion (EDT = UTC-4)
- 9:31 AM ET = 13:31 UTC | 9:45 AM = 13:45 | 10:00 = 14:00 | 10:15 = 14:15 | 10:30 = 14:30 | 12:00 PM = 16:00 | 3:45 PM = 19:45 | 4:00 PM = 20:00

## ⚠️ CRITICAL: Dev DB ≠ Production DB
Dev and production use COMPLETELY SEPARATE PostgreSQL databases. Data inserted via dev server (manual scans, curl to localhost) does NOT appear in production.

**EOD Sweep tab shows no data if production DB has no records for today.** This happens when:
1. The production server deployed AFTER the scheduled scans (3:30–4:15 PM ET)
2. The server was restarted mid-day

**Fix**: Hit `GET https://nclexai.org/stock-api/unusual-calls` from a detached process — it runs a live scan and saves results to the production DB with `last_seen = NOW()`. Takes ~2–3 minutes.

**Better fix** (deployed): `POST https://nclexai.org/stock-api/admin/run-eod-scan` — starts scan in background, returns immediately.

## EOD Sweep Endpoint (`/stock-api/eod-sweeps`)
- Line 6441 in main.py
- Today-first SQL: `WHERE last_seen::date = CURRENT_DATE AND EXTRACT(HOUR FROM last_seen AT TIME ZONE 'UTC') BETWEEN 14 AND 23`
- Fallback: last 5 days in same hour window
- Cache: 120s TTL, busted with `?bust=1`
- Admin trigger: `POST /stock-api/admin/run-eod-scan` (line 6588) — runs scan in background thread

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
- DEFAULT_LEADERBOARD = ~1,433 tickers for all scans
- Use `UUP` (not `^DXY` — delisted on yfinance) for USD strength
- GEX uses numpy normal PDF — no scipy dependency
- FREE_LIMIT on NCLEX is 10 — never change

## EOD Accumulation Scanner — Key Design Decisions (June 2026)
- Gate 1: `price_chg >= -20%` (NOT "up on day") — accumulation happens on flat/down days too
- Gate 2: `closing_range >= 0.50` (NOT 0.65) — close above midpoint = net accumulation
- Gate 3: `eod_rel_vol >= 2.5×` — last-30-min vol vs normal EOD vol
- Gate 4: `late_flow >= 2.0×` — buy:sell ratio in the 3:30-4:00 PM window only
- Gate 5: `quiet_surge >= 1.5×` (HARD GATE) — EOD vol/min must be 1.5× busier than midday
- Universe: watchlist + unusual_calls_log (DATE(first_seen)=today) + Yahoo screener (up ≥1%, ≥$10M)
- **Why**: FTRK was -2% on the day with 8.8× EOD vol, 13.6× late flow → ran +25% next morning; old gate blocked it

## EOD Short Squeeze Setup Signal (added June 2026)
- **Pattern**: eod_rel_vol ≥ 50×, late_flow < 2.0× (sellers winning), closing_range < 0.50, price ≥ $1, mkt_cap ≥ $20M
- **Opposite of accumulation** — shorts loading in at close on massive volume, weak close
- **Forensic proof**: June 10→11 backtest: MNTS -9.4%→+45%, VELO +15%→+37%, ASTI→+30%, SPCE→+28%, LUNR→+16%, FLY→+17%
- All showed 100-800× EOD vol with sellers winning and weak close — zero showed unusual calls the day before
- `signal_type = "squeeze"` in response; `squeeze_setups` is a separate array in the API response
- Displayed as 🩳 SHORT SQUEEZE SETUPS section (red cards) below the accumulation section
- Squeeze score = raw eod_rel_vol (higher = more shorts loaded)

## Morning Watchlist (37 tickers as of 2026-06-10)
['AXTI','AZI','BATL','BBGI','BULL','CASY','CBRL','CMCT','CRE','DSY','DWSN','FJET','FLL','FRMI','HCAI','HPK','INDP','JEM','LAKE','LICN','LMNR','LUCK','MAAS','OCC','OPTX','PLAY','PW','RETO','SCLX','SDOT','SPHL','STAK','STI','TGL','TTRX','VSME','WTI']
- Watchlist checked at every scan (9:31/9:45/10:00/10:15/10:30) regardless of gap size
- Keep under ~100 tickers — each requires individual yfinance call, 350+ would cause scan overlap
- Grow by adding tickers user reports each evening that the scanner missed

## SMS Alert Scoring & Routing (updated June 13 2026)
- Options-based routing (NOT market cap): `has options → _with_options_score (threshold 50)`, `no options → _no_options_score (threshold 60)`
- Cap label in SMS (display only): MICRO/SMALL/MID/LARGE CAP + "has options" / "no options"
- Morning Burst (9:31–9:45 AM): fires on any size move, no % cap
- Midday scanner filters: max +5% from prev close, must be 2%+ below HOD, momentum_15m ≥ 2%, min +2% from open, RVOL ≥ 2x
- Gap Recovery: momentum_15m ≥ 1.5%, pullback ≥ 3%, VWAP reclaim required
- Scoring: `_with_options_score` (RVOL 30pts, chg 25pts, VWAP 20pts, ORB 10pts, gap 8pts, ATR 7pts), `_no_options_score` (RVOL 25pts, float_turnover 20pts, VWAP 15pts, gap 10pts, ORB 10pts, chg 8pts, ATR 7pts, short 5pts)

## Pre-Close Swing Scanner (added June 13 2026)
- `artifacts/stock-scanner-api/eod_swing.py` — EOD swing setup scanner
- Fires at 3:30 PM ET Mon-Fri — 30 min before close so user can enter same day
- Universe: Barchart top movers up 2%+ across all 4 cap tiers (~200 tickers)
- 3-day lookback (not 5 — catches setups earlier, better R/R)
- Scoring (100 pts, threshold 60): close position in range (25), peak RVOL (25), 3d momentum (20), pullback quality (15), options PCR (10), above 20d MA (5)
- Must-have gates: close in top 60%+ of range, 3d momentum ≥ 3%
- SMS: all qualifying setups in ONE text (format: ticker/price/chg/score/3d-gain/PCR)
- Exit rule discussed: sell gap Day 4 morning (half), trail VWAP on rest, out by Day 5

## SMS / Twilio
- `artifacts/stock-scanner-api/sms_alerts.py` — complete SMS system, fully wired into main.py
- Scheduler: every 15 min, Mon-Fri 9:30 AM–3:45 PM ET
- One text per ticker per day (deduped via sms_alerts_log table UNIQUE constraint on ticker+date)
- User phone: +14013185787 (T-Mobile), gateway: 4013185787@tmomail.net

## Email alerts
- `artifacts/stock-scanner-api/email_alerts.py` — full email digest
- SMTP not configured — needs `SMTP_USER` + `SMTP_PASS` secrets
- Subscriber id=2 is joeldcarlo@gmail.com

## Key design decisions
- Default tab on load is "lookup" (Stock Lookup)
- `top_prem_value_k` is in $K units in the options_summary response
- BB_BG="#060c14", BB_PANEL="#0b1320", accent green="#22c55e", monospace terminal font

**Why:** User asked to preserve all work across sessions so nothing is lost between conversations.
