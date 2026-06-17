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

## 7-Layer Conviction Stack — UPGRADED June 16 2026
- **Scanner universe**: ARQQ, BTQ, QUBT, NTLA, BEAM, EDIT, CRSP, FATE, BLUE, CIFR, IREN, WULF, CORZ, BITF added to DEFAULT_LEADERBOARD in smart_money.py
- **L7 OTM filter fix**: `_run_microcap_options_scan()._scan_one()` — expiry window extended 45d → 365d; calls >40% OTM now pass if vol/OI ≥5× AND prem ≥$200K; `far_otm_sweep=True` flag added to hit dict; urgency now includes MEDIUM (21-60d) and FAR (60d+) labels
- **DB**: `far_otm_sweep BOOLEAN DEFAULT FALSE` column added to `unusual_calls_microcap_log` (ALTER TABLE IF NOT EXISTS migration in `_init_microcap_calls_table()`)
- **SECTOR_MAP**: 10 themes (quantum_computing, crypto_mining, gene_editing, ai_infrastructure, ev_space, meme_squeeze, clean_energy, biotech_catalyst, fintech_crypto, small_float_spec) in main.py at module level
- **L6 `_get_float_pressure_signals(tickers)`**: fetches float + total call OI, computes (call_OI×100×0.4) / float × 100 → flags >2%; 0-2 pts
- **L7 `_get_far_otm_sweeps(days_back)`**: queries unusual_calls_microcap_log WHERE far_otm_sweep=TRUE; 0-2 pts based on vol/OI ratio
- **L8 `_get_sector_heat(days_back)`**: cross-references fired tickers against SECTOR_MAP → sympathy plays in same sector; 0-1.5 pts
- **`_run_five_layer_conviction()`**: now runs L6/L7/L8 after L5; L7/L8 can INTRODUCE new tickers not in L1-L5; score normalized to 10 (max 14 raw pts)
- **3 new API endpoints**: `/stock-api/float-pressure`, `/stock-api/far-otm-sweeps`, `/stock-api/sector-heat`
- **Morning SMS**: now includes "🔍 FAR-OTM SWEEPS" section (L7) and "🔥 SECTOR HEAT" section (L8)
- **Dashboard tabs**: "🔍 SWEEP RADAR" (id: sweepradar → FarOtmSweepTab) and "🌡️ SECTOR HEAT" (id: sectorheat → SectorHeatTab) added
- **ConvictionStackTab**: updated to show L1–L8 legend (4-col grid), new scoring explanation
- **Conviction stack tab label**: changed from "🎯 5-LAYER CONVICTION" to "🎯 7-LAYER CONVICTION"
- **api.ts**: `fetchFarOtmSweeps`, `fetchSectorHeat`, `fetchFloatPressure` + full TypeScript interfaces added

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
- DEFAULT_LEADERBOARD = 6,610 tickers (full CBOE optionable universe, June 10 2026). Morning polls cap at ~1,200/scan — see scanner-data-source-ceiling.
- Use `UUP` (not `^DXY` — delisted on yfinance) for USD strength
- GEX uses numpy normal PDF — no scipy dependency
- FREE_LIMIT on NCLEX is 10 — never change

## Composite Score universe scan (June 16 2026)
- User wants the **Composite Score (/10) ONLY** — explicitly rejected the Smart Money score (/100). Composite = `compute_indicators(df)` + `compute_score(indicators)` from a 1y price df (RSI 40–60 best, MACD, trend price>SMA50>SMA200, volume ratio, Bollinger; 2 pts each → normalized /10). No options / no `.info` needed.
- `composite_scan.py` runs it across the full DEFAULT_LEADERBOARD (6,610) inside the stock-api process; stores daily to `composite_score_history` (unique on scan_date,ticker). Endpoints: `POST /stock-api/composite-scan/trigger`, `/stock-api/composite-scan/status`, `/stock-api/composite-leaderboard?min=N`.
- **8+ is NOT a shortlist**: ~514–578 names hit 8+, dominated by ETFs/funds and below-average-volume names. The actionable cut = score≥8 AND volume_ratio≥1.5 (~54 names), which fits the user's "forced buying" thesis. Many top scorers are ETFs (AMZO, CEW, IPO, UST…) — exclude funds for a pure single-name list.
- NOT yet built (offered to user): daily scheduler job + frontend Composite Leaderboard screen + ETF/fund exclusion.

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
- `composite_score_history` — daily Composite Score (/10) for the full universe; UNIQUE (scan_date, ticker); cols: scan_date, ticker, score, rating, price, rsi, volume_ratio, price_change_pct, scanned_at

## Owner-personal intraday alert emails (added June 17 2026)
- **What the owner (Joel) asked for**: he wants to receive emails to HIMSELF (joeldcarlo@gmail.com) every morning AND throughout the day for three alert types — (1) micro/small-cap calls, (2) high-conviction picks, (3) Smart Money Pressure signals scored /10 — WITHOUT increasing what paying customers receive.
- **Decision: owner_only seam, not a new cadence for customers.** `_send_microcap_calls_email`/`_send_high_conviction_email` take `owner_only=True` → recipients = `[{"email": _OWNER_EMAIL}]` (`_OWNER_EMAIL = ALERT_EMAIL env, default joeldcarlo@gmail.com`). Customer-facing default calls are untouched.
- **Smart Money Pressure /10 email** (`_send_smart_money_pressure_email`): emails owner every signal ≥6 (EXTREME 8+, HIGH 6-7.9) from the L1-L8 engine, each with a concrete trade next to the score. `_expiry_recommendation(score,dtc)`: 8+ → ~2-wk call; 6-7.9 → call window scaled by days-to-cover; 4-5.9 → stock. `_scan_best_call(ticker,price,target_weeks)` finds a real liquid call biased to that week window; email DISPLAYS the actual expiry weeks (not the target) so headline strike+expiry never contradicts the stated window. Only sends if ≥1 signal ≥6.
- **Why score→trade matters**: owner explicitly required "show the concrete trade next to the score — specific strike + expiration + buy-call-vs-stock, scaled by score." Do NOT regress this into a bare score list.
- **Schedule (ET, Mon-Fri)**: smart-money owner emails 10:05/12:00/14:00/15:40 (email only, intraday max_tickers=35) + EOD 16:50 (snapshot+email, max_tickers=60). Owner micro-cap copies 9:50/11:35/13:05/14:35/15:45; owner high-conviction copies 9:52/11:37/13:07/14:37/15:47.
- **EOD snapshot semantics preserved**: only the 16:50 run persists the conviction-stack track record; intraday runs email-only. `snapshot_conviction_stack(precomputed=...)` reuses one scan for both snapshot + email.
- **Rate-limit guards**: module `_CONVICTION_SCAN_LOCK` (skip if a scan already running) + `_BEST_CALL_CACHE` (45-min per ticker/target_weeks) so staggered jobs don't re-hammer yfinance.
- **⚠️ GOES LIVE ONLY ON REPUBLISH.** Code is on main/dev but emails fire only from the published app. Dev sandbox BLOCKS outbound SMTP (port 587) → emails CANNOT be e2e-tested in dev. Scheduler needs always-on Reserved VM (Autoscale spins down → missed jobs). The one action the owner must take: **republish from a computer as a Reserved VM.** (See stockscanner-deployment.md.)

### Wake-up catch-up backup (added June 17 2026) — stopgap for Autoscale
- **Purpose**: until the owner republishes as a Reserved VM, Autoscale sleeps and misses the cron emails. So opening the website now fires any of today's owner emails that were due-but-unsent. **This is a STOPGAP; the real fix is still Reserved VM.**
- **Shared dedup invariant (the critical part)**: `_OWNER_EMAIL_SCHEDULE` (kind→ET (h,m) slots, Mon-Fri) + `_EOD_SMART_MONEY_SLOT=(16,50)` are the SINGLE source of truth used by BOTH the real-time scheduler AND the catch-up. Every send claims a `owner_email_log` row `UNIQUE(kind,slot,sent_date)` via INSERT…ON CONFLICT DO NOTHING (rowcount>0 = we own it) BEFORE sending. **Why:** this is the only thing stopping the scheduler (on a Reserved VM) and the wake-up catch-up from double-emailing the same slot. If you ever add a new owner-email cadence, it MUST route through `_owner_claim_slot` or duplicates return.
- **Trigger**: `@app.before_request` hook fires the catch-up in a daemon thread on ANY request (throttled 120s; skips OPTIONS + the manual endpoint). Manual/test: `GET|POST /stock-api/admin/owner-catchup`.
- **Collapse behavior**: catch-up claims ALL past-due slots for a kind but sends only ONE current email (visiting at 3pm → one fresh email, not a 5-email backlog burst). Scheduler still sends per-slot in real time. `_OWNER_CATCHUP_LOCK` (process-local, non-blocking) serializes concurrent wake-ups so they can't split slots and each send a collapsed email.
- **Accepted tradeoff (claim-before-send)**: a rare transient SMTP failure consumes that slot without an email, BUT the next window's slot resends a fresh email, so it self-heals within a window — never an all-day loss. Deliberately did NOT build a pending/sent/failed retry state machine: it's untestable in dev (SMTP blocked) and would add more risk than it removes for a stopgap.
- **smart_money in catch-up** runs the L1-L8 engine; serialized via `_CONVICTION_SCAN_LOCK` (skip if busy — another scan will send). Catch-up does NOT snapshot the track record (snapshot stays scheduler-EOD-only); the owner still gets the email, just not the snapshot, if on Autoscale.

### News-catalyst wake-up backup (added June 17 2026) — same Autoscale stopgap
- **Purpose**: morning NEWS CATALYST emails fire 9:31–10:30 ET, but a sleeping Autoscale server misses them (owner got an OCUL catalyst email at 10:44 instead of 9:31). Second `@app.before_request` hook `_news_catchup_on_wake` runs a fresh news scan on ANY request, weekdays, 9:31–16:00 ET, throttled 5 min, in a daemon thread via `_news_run_due_scan` (non-blocking `_NEWS_CATCHUP_LOCK`). Manual/test: `GET|POST /stock-api/admin/news-catchup`.
- **⚠️ CRITICAL gotcha**: `run_news_catalyst_scan()` self-gates to 9:31–10:30 ET and returns `[]` outside it. Any catch-up/backup caller MUST pass `force=True` or it silently no-ops (was the original bug — admin endpoint returned `{"status":"ok"}` while scanning nothing). The scan math keys off `now_et`, so a forced late scan just measures 9:30→now; the per-ticker `news_catalyst_log` dedup makes re-runs safe (only NEW catalysts email). Weekends still skip even with force.
- **Security note (accepted)**: `/stock-api/admin/news-catchup` is unauthenticated, matching the existing `/stock-api/admin/owner-catchup` convention; the before_request hook already triggers the scan publicly by design, and the lock (held for the scan's full ~60s) + dedup naturally rate-limit abuse. Not worth a bespoke auth scheme for a personal tool.

## ⚠️ CRITICAL: Dev DB ≠ Production DB
Dev and production use COMPLETELY SEPARATE PostgreSQL databases.
**Fix**: `POST https://nclexai.org/stock-api/admin/run-eod-scan` — starts scan in background.

## Key design decisions
- **Heavy /stock-api endpoints (>~30s scan) MUST be stale-while-revalidate, never synchronous.** Pattern: serve cache instantly; if stale, return it with `refreshing:true` + kick a single-flight bg worker (process-local non-blocking `Lock` + `_generating` flag, initialized at startup so two cold requests can't each spawn a scan); if cold, return `{warming:true, <empty buckets>, scanned:0}` immediately. Frontend polls every ~7s while `warming||refreshing`. **Why:** a synchronous scan blows past the iOS/proxy ~60s timeout → WebKit shows "Load failed" (this was the Micro/Mid Net Flow bug); far worse on Autoscale where in-memory caches wipe on sleep, so the FIRST request after wake always cold-scans. Route scheduler prewarm through the SAME worker so prewarm + on-demand can't double-scan yfinance.
- Default tab on load is "lookup" (Stock Lookup)
- BB_BG="#060c14", BB_PANEL="#0b1320", accent green="#22c55e", monospace terminal font
- yfinance multi-ticker column order: `df[ticker][metric]` NOT `df[metric][ticker]`
- Backend port 5050; api-server port 8080
