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
1. 🔥 Morning burst texts at 9:35, 9:40, 9:45 AM (cron, fixed times)
2. 📰 News Catalyst text (if news gap fires) → different setup type
3. 📶 Steady Grinder texts 10:30 AM–1:30 PM (every 30 min)
4. 🎯 +10% profit target text → set trailing stop, let it run
5. ⚠️ VWAP break exit text → tighten stop or exit
- SMS: primary via 4013185787@tmomail.net (T-Mobile email-to-SMS), backup joeldcarlo@gmail.com
- User phone: +14013185787 (T-Mobile)

## Morning Burst Scanner (run_sms_alert_scan) — UPDATED June 14 2026
- **Schedule**: cron at `hour=9, minute="35,40,45"` — exactly 3 scans per day
- **Window**: 9:35–9:50 AM only (market_close set to 9:50 in function)
- **RVOL threshold**: sliding gate — RVOL≥2x for large/mid-cap chg 3-7%, RVOL≥1.5x for chg≥20%, etc.
- **Sector ETF check**: pre-fetches SMH/XLK/XLE/XLF/XLV/XLY/XLP/XLI/XLC/XLB once per scan run; uses `info.sector` + `info.industry` from yfinance; "Semiconductor" in industry → checks SMH; blocks signal if sector ETF is red on the day
- **NEW gate (Jun 14 2026): gap_pct > 4% → skip** — stock ran its move pre-market; 9:35 burst is retail FOMO. 6 of 8 worst losses were gap>4%. Filter alone → 67.9% WR.
- **NEW gate (Jun 14 2026): vwap_ext > 2% → skip** — chasing extended move. LRCX Jun10 vwap+3.6%→-5.97%, INTC Jun10 vwap+2.7%→-3.58%. Combined with gap cap → ~70-72% WR.
- **DO NOT add XLV/XLY block to morning burst** — XLV = 100% WR in morning (FDA/earnings follow-through). Opposite of grinder.
- **SPY insight**: winning signals fire when SPY is flat. When SPY is already positive at 9:35, stocks are riding the market and tend to fade. No filter added (removing SPY-negative removes 12 winners for only 5 losers).
- **Backtest (Jun 1–13, 10 days) WITH new filters**: ~70-72% WR (est. 25 signals from 40)
- **Key insight**: waiting until 10:00 AM does NOT help — win rate drops from 62% to 57%. Fire at 9:35.

## Steady Grinder Scanner — UPDATED June 14 2026
- `run_steady_grinder_scan()` in `artifacts/stock-scanner-api/sms_alerts.py`
- **Schedule**: every 30 min, 10:30 AM–1:30 PM ET
- **All gates**: avg daily vol ≥ 1M, price ≥ $10, chg 2-8%, RVOL 1.3-3.0x, t45 0.5-2.0%, above VWAP (ext ≤3%), no single bar >40% vol, HOD within 2%, EMA9>EMA21 on 30-min, dual 45-min trend
- **NEW gate (Jun 14 2026): gain-from-open < 3%** — stocks already up >3% from open have run their race; belong in morning burst, not grinder
- **NEW gate (Jun 14 2026): block XLV + XLY** — healthcare/consumer = 0% WR across 6 signals (LLY x2, AMGN, PFE, NKE, MELI all losers). These sectors gap at open and fade, not grinders.
- **Backtest (Jun 1–13, 10 days) WITH new filters**: 68.8% WR (16 signals, from 50%/28), EV +0.541%/trade (from -0.075%)
  - Removed 9 losers (-17.86% total avoided), cost 3 winners (+7.12% missed) — net +10.74%
- **Signal profile**: pure day trade, NOT overnight
- **Best sectors**: XLK (88% WR), SMH (60% WR) — stick to tech and semis
- Uses `sms_midday_log` table with alert_type='grinder' for dedup; text label: "📶 STEADY GRINDER"

## Backtest Scripts
- `artifacts/stock-scanner-api/backtest_week.py` — grinder only, Jun 1-5 (5-min bars)
- `artifacts/stock-scanner-api/backtest_morning_vs_grinder.py` — **two-week comparison**: morning scanner (early 9:30-10:00 / late 10:00-10:30) vs grinder; Jun 1-5 + Jun 9-13
- `artifacts/stock-scanner-api/backtest_results.py` — targeted post-signal performance checker
- `artifacts/stock-scanner-api/backtest_grinder.py` — older grinder backtest

## Two-Week Head-to-Head (Jun 1–13, 2026)
| Scanner | Signals | Win Rate | Avg Win | Avg Loss |
|---|---|---|---|---|
| Early morning 9:35-10:00 | 45 | 62% | +2.55% | -3.33% |
| Late morning 10:00-10:30 | 7 | 57% | +1.59% | -1.84% |
| Steady Grinder 10:30 | 28 | 50% | +1.50% | -1.65% |

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
- `signal_type = "squeeze"` in response; `squeeze_setups` is a separate array in the API response
- Displayed as 🩳 SHORT SQUEEZE SETUPS section (red cards) below the accumulation section

## Pre-Close Swing Scanner (added June 13 2026)
- `artifacts/stock-scanner-api/eod_swing.py` — EOD swing setup scanner
- Fires at 3:30 PM ET Mon-Fri — 30 min before close so user can enter same day
- Universe: Barchart top movers up 2%+ across all 4 cap tiers (~200 tickers)
- Scoring (100 pts, threshold 60): close position in range (25), peak RVOL (25), 3d momentum (20), pullback quality (15), options PCR (10), above 20d MA (5)
- SMS: all qualifying setups in ONE text (format: ticker/price/chg/score/3d-gain/PCR)
- **Exit rule (backtested June 14 2026): D+1 close ONLY** — holding 3 days is wrong
  - D+1: 71% WR, avg win +3.97%, avg loss -9.47%, EV +0.13%/trade (n=7)
  - D+3: 0% WR, avg loss -10.43%/trade (n=4, all from tech-correction week Jun 1-5)
  - SMS and email both updated to say "Exit: next-day close. Stop: below 3d low."
  - Signal frequency: ~1 signal/day (much lower than morning burst's ~4-5/day)

## SMS / Twilio
- `artifacts/stock-scanner-api/sms_alerts.py` — complete SMS system, fully wired into main.py
- **Morning burst**: cron `hour=9, minute="35,40,45"` — exactly 9:35, 9:40, 9:45 AM
- **Grinder**: every 30 min, 10:30 AM–1:30 PM ET
- **SPY green-day filter active on ALL scan types**
- One text per ticker per day (deduped via sms_alerts_log table UNIQUE constraint on ticker+date)
- User phone: +14013185787 (T-Mobile), gateway: 4013185787@tmomail.net

## DB Tables (PostgreSQL)
- `ai_trade_log` — every GPT trade pick stored for track record
- `signal_history` — daily signal snapshots; has `scan_time` (timestamptz, UTC); ticker, scan_date, price_chg_pct, rel_vol, flow_ratio, standout_score
- `signal_outcomes` — T+3/5/10 price outcomes for win rate tracking
- `daily_vol_snapshots` — daily iv_skew, short_float, pc_oi_ratio, pc_prem_ratio, rs_vs_spy per ticker
- `daily_top10`, `answers`, `questions`, `score_history`, `sessions`, `sm_subscribers`
- `unusual_calls_log` — per-tick options flow; date column is `first_seen` (NOT `log_date`)
- `morning_inflows_cache` — columns: scan_date, payload (JSON), saved_at; ONE row per day
- `eod_accum_picks` — columns: scan_date, ticker, close_price, accum_score, eod_rel_vol, closing_range, late_flow, news_headline
- `eod_accum_outcomes` — tracks next-day outcomes of EOD accum picks
- `sms_alerts_log` — ICS SMS alerts (ticker+date unique)
- `sms_profit_log` — +10% profit target alerts (ticker+date unique)
- `news_catalyst_log` — news catalyst SMS alerts (ticker+date unique)

## ⚠️ CRITICAL: Dev DB ≠ Production DB
Dev and production use COMPLETELY SEPARATE PostgreSQL databases.
**Fix**: `POST https://nclexai.org/stock-api/admin/run-eod-scan` — starts scan in background, returns immediately.

## Key design decisions
- Default tab on load is "lookup" (Stock Lookup)
- `top_prem_value_k` is in $K units in the options_summary response
- BB_BG="#060c14", BB_PANEL="#0b1320", accent green="#22c55e", monospace terminal font
- yfinance multi-ticker column order: `df[ticker][metric]` NOT `df[metric][ticker]`

**Why:** User asked to preserve all work across sessions so nothing is lost between conversations.
