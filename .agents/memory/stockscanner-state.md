---
name: StockScanner AI state
description: Full state of the StockScanner AI product — landing page, Stripe, SMS, key files, architecture
---

## Architecture
- React+Vite frontend: `artifacts/stock-scanner/` — preview at `/stock-scanner/`
- Python Flask API: `artifacts/stock-scanner-api/main.py` — port 5050, workflow: "artifacts/stock-scanner: stock-api"
- Node.js API server: `artifacts/api-server/` — port 8080, handles Stripe checkout + webhooks
- APScheduler unusual_calls scans: 9:30, 10:00, 11:30 AM, 1:00, 2:30, 3:30, 3:45, 4:00, 4:15 PM ET every trading day
- Morning inflows scans: 9:31, 9:33, 9:35, 9:38, 9:41, 9:45, 10:00, 10:15, 10:30 AM ET
- Email schedule (Mon-Fri ET): morning_inflows 9:46/10:01/10:16/10:31 AM | eod_accum 3:46 PM | ai_trades 10:00 AM | unusual_calls 9:47 AM + 3:15 PM | microcap_calls 10:32 AM + 3:16 PM | high_conviction 9:48 AM + 3:17 PM
- Position monitor: `position_monitor` DB table; exit signal checker every 30 min; fires at score ≥3
- Signal snapshot job at 4:00 PM ET (saves to signal_history table)
- Daily vol snapshot job at 4:05 PM ET (saves to daily_vol_snapshots table)
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
- 4 signals (100 pts total): Blowout RVOL >15x(30), News keyword(20), Recovery confirmation(25), Sustained volume 3x+(25)
- SMS threshold: 75+. Text labeled "📰 NEWS CATALYST"
- DB table: `news_catalyst_log` (ticker, alert_date unique constraint, price, score, catalyst)

## SMS Alert Flow (Monday morning)
1. 🔥 Morning burst texts at 9:35, 9:40, 9:45 AM (cron, fixed times)
2. 📰 News Catalyst text (if news gap fires)
3. 📶 Steady Grinder texts 10:30 AM–1:30 PM (every 30 min)
4. 🎯 +10% profit target text → set trailing stop, let it run
5. ⚠️ VWAP break exit text → tighten stop or exit
- SMS: primary via 4013185787@tmomail.net (T-Mobile email-to-SMS), backup joeldcarlo@gmail.com
- User phone: +14013185787 (T-Mobile)

## Morning Burst Scanner — UPDATED June 14 2026
- **Schedule**: cron at `hour=9, minute="35,40,45"` — exactly 3 scans per day
- **Window**: 9:35–9:50 AM only (market_close set to 9:50 in function)
- **NEW gate: gap_pct > 4% → skip** — 6 of 8 worst losses were gap>4%. Filter → 67.9% WR.
- **NEW gate: vwap_ext > 2% → skip** — chasing extended move. Combined → ~70-72% WR.
- **DO NOT add XLV/XLY block to morning burst** — XLV = 100% WR in morning (FDA/earnings follow-through)
- **Backtest (Jun 1–13) WITH new filters**: ~70-72% WR (est. 25 signals from 40)

## Steady Grinder Scanner — UPDATED June 14 2026
- `run_steady_grinder_scan()` in `artifacts/stock-scanner-api/sms_alerts.py`
- **Schedule**: every 30 min, 10:30 AM–1:30 PM ET
- **NEW gate: gain-from-open < 3%** — stocks already up >3% have run their race
- **NEW gate: block XLV + XLY** — 0% WR across 6 signals in these sectors
- **Backtest (Jun 1–13) WITH new filters**: 68.8% WR (16 signals), EV +0.541%/trade

## Track Record System (NEW — June 14 2026)
- **High Conviction Track Record** (conviction_calls_snapshot → conviction_calls_outcomes):
  - Snapshot at 4:25 PM ET: saves EXTREME/HIGH picks from unusual_calls_log (vol/OI ≥5x, prem ≥$500K, ≤30d)
  - Outcomes at 4:32 PM ET: fills D+1/D+3/D+5 % price change vs entry via yfinance
  - Table: `conviction_calls_outcomes` (snap_date, ticker, conviction, score, entry_price, d1_pct, d3_pct, d5_pct)
  - API: `/stock-api/conviction-outcomes` → returns picks + win rates + EV per conviction level
  - UI: 📊 TRACK RECORD panel inside ConvictionCallsTab (win rate cards + picks table)
- **Bull Flow Track Record** (signal_outcomes table, pre-existing):
  - Stores C/P ≥2x bull flow signals; outcomes computed live via yfinance (T+3/T+5/T+10)
  - API: `/stock-api/outcomes` (get_signal_outcomes function in signal_outcomes.py)
  - UI: 📊 Track Record panel inside BullFlowTab

## 🔥🐋 Whale + High Conviction Dual-Signal SMS (NEW — June 14 2026)
- **Function**: `_check_whale_hc_crossover()` in main.py
- **Trigger**: every 30 min, 10:00 AM–3:30 PM ET (scheduler id: `whale_hc_crossover`)
- **Logic**: finds tickers in BOTH whale_blocks (LEAPS CALL, ≥$5M, last 24h) AND unusual_calls_log (vol/OI ≥5x, prem ≥$500K, today)
- **De-dupe**: `_whale_hc_alerted` module-level dict {date_str: set(ticker)} — one SMS per ticker per day
- **SMS format**:
  ```
  🔥🐋 DUAL SIGNAL: $TICKER
  🐋 Whale TIER: $Xm LEAPS CALL · Xd · $STRIKE strike exp EXPIRY
  ⚡ HC EXTREME🔥/HIGH⚡: X.Xx vol/OI · $XM prem
  📌 Short-term play: $STRIKEC exp EXPIRY (Xd)
  Both long-term whale + short-term smart money bullish on TICKER
  ```
- **Key distinction**: Whale = raw $ size (90-365d LEAPS), HC = vol/OI ratio (1-30d sweeps). Different methods, same ticker = strongest signal.

## Stripe
- Product: "StockScanner AI Pro" — Price: `price_1TfQfiChn3bmMDTvww8LpUIn` ($59/mo, ACTIVE)
- OLD prices deactivated: $39/mo and $29/mo — do not use
- Checkout route: `artifacts/api-server/src/routes/stripe.ts` at `/stock-scanner/checkout`
- Webhook handler: `artifacts/api-server/src/webhookHandlers.ts`

## Landing Page
- `artifacts/stock-scanner/src/pages/Landing.tsx` — "16 scanners. One AI thesis."
- $59/mo price, crossed-out $79, "🔥 Limited Time — Price goes up soon" badge

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

## EOD Short Squeeze Setup Signal (added June 2026)
- **Pattern**: eod_rel_vol ≥ 50×, late_flow < 2.0× (sellers winning), closing_range < 0.50
- `signal_type = "squeeze"` in response; displayed as 🩳 SHORT SQUEEZE SETUPS (red cards)

## Pre-Close Swing Scanner — UPDATED June 14 2026
- **Trigger: 2:00 PM ET** (was 3:30 PM — SMS arrived after close; useless)
- **Exit rule (backtested)**: D+1 close ONLY — D+3 had 0% WR during tech-correction week

## OPTIONS — HIGH Conviction Filter (June 14 2026)
- Backtested 45 expired Jun 12 contracts → HIGH = 91% WR (11 signals), MEDIUM = 59% WR (34 signals)
- Only HIGH conviction signals sent going forward
- Unusual calls alert threshold: vol/OI ≥5x + premium ≥$100K
- HIGH CONVICTION tab: filtered to EXTREME + HIGH only (EXTREME ≥12, HIGH ≥7)
- HIGH CONVICTION tab expiry filter: **1–30 days only** — LEAPS never appear here

## DB Tables (PostgreSQL)
- `ai_trade_log` — every GPT trade pick stored for track record
- `signal_history` — daily signal snapshots
- `signal_outcomes` — T+3/5/10 price outcomes for bull flow win rate tracking
- `conviction_calls_snapshot` — 4:25 PM daily snapshot of EXTREME/HIGH options picks
- `conviction_calls_outcomes` — D+1/D+3/D+5 outcomes for conviction picks
- `daily_vol_snapshots` — daily iv_skew, short_float, pc_oi_ratio per ticker
- `unusual_calls_log` — per-tick options flow; date column is `log_date` (also `first_seen`)
- `morning_inflows_cache` — columns: scan_date, payload (JSON), saved_at; ONE row per day
- `eod_accum_picks` — EOD accumulation scanner results
- `eod_accum_outcomes` — next-day outcomes of EOD accum picks
- `whale_blocks` — whale LEAPS/aggressive/medium option blocks; columns: ticker, direction, strike, expiry, days_out, prem_m, volume, otm_pct, category (LEAPS/AGGRESSIVE/MEDIUM), tier (MEGA_WHALE/WHALE/BIG_BLOCK), price, first_seen
- `sms_alerts_log` — ICS SMS alerts (ticker+date unique)
- `news_catalyst_log` — news catalyst SMS alerts (ticker+date unique)

## ⚠️ CRITICAL: Dev DB ≠ Production DB
Dev and production use COMPLETELY SEPARATE PostgreSQL databases.
**Fix**: `POST https://nclexai.org/stock-api/admin/run-eod-scan` — starts scan in background.

## Key design decisions
- Default tab on load is "lookup" (Stock Lookup)
- BB_BG="#060c14", BB_PANEL="#0b1320", accent green="#22c55e", monospace terminal font
- yfinance multi-ticker column order: `df[ticker][metric]` NOT `df[metric][ticker]`
- Backend port 5050; api-server port 8080
