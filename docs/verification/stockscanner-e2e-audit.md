# StockScanner AI — Complete End-to-End Audit (2026-08-05)

**Site:** https://nclexai.org (StockScanner AI — separate from AIEM Options Engine)  
**Code:** `artifacts/stock-scanner/` (FE) + `artifacts/stock-scanner-api/` (BE)  
**Live probe:** `docs/verification/stockscanner-e2e-live-probe.json`  
**Math detail:** `docs/verification/stockscanner-indicator-pattern-math-audit.md`  
**Branch / PR:** `cursor/stockscanner-tabs-audit-5ace`

Probed after market close ET on 2026-08-05. Empty/stale on Yahoo-backed tabs is partly expected after hours; structural FAIL/math bugs are not.

---

## 1. Verdict (one page)

| Area | Status | Notes |
|------|--------|-------|
| Site up | **OK** | `/stock-api/health` → 200 |
| Tab surface | **81 dropdown tabs** | Almost all live in `Dashboard.tsx`; 2 orphan components (`backtest`, `alerts`) not in nav |
| Live API (74 routes) | **37 OK · 5 STALE · 27 EMPTY · 4 FAIL · 1 ERR** | See §3 |
| Math / indicators | **Mostly CORRECT; 5 bugs fixed in this PR** | See §5 |
| Trade history storage | **PARTIAL** | Calls/sweeps/HC/0DTE persist; Puts history endpoint missing; Insider outcomes often empty |
| Schedulers | **~100+ jobs registered** | Unusual calls, EOD sweep, conviction, 0DTE paper, gamma, nano, AIEM paper all wired in code |

**Bottom line:** The site is not “dead,” but several tabs the user flagged are structurally broken or misleading (Unusual Puts history, Insider Radar dump/outcomes, Bear Flow score-trace, 0DTE stop copy, Sweep flat-return nulls). Yahoo throttle leaves many non-options tabs empty after hours.

---

## 2. Architecture

```
Browser (nclexai.org/app)
  └─ Dashboard.tsx  (81-tab <select>)
       └─ GET/POST /stock-api/*
            └─ Flask main.py
                 ├─ Postgres (Neon) tables for logs/outcomes
                 ├─ APScheduler (~100 jobs, America/New_York)
                 └─ Polygon / Tradier / Yahoo feeds
```

| Layer | Path |
|-------|------|
| Frontend shell | `artifacts/stock-scanner/src/pages/Dashboard.tsx` |
| API client | `artifacts/stock-scanner/src/lib/api.ts` (`BASE=/stock-api`) |
| Backend | `artifacts/stock-scanner-api/main.py` |
| 0DTE paper | `artifacts/stock-scanner-api/patterns/zero_dte_sweep.py` |

---

## 3. Live API probe (2026-08-05 evening ET)

### 3.1 FAIL (broken routes)

| Code | Method | Route | Impact |
|-----:|--------|-------|--------|
| 404 | GET | `/unusual-puts-log` | **No Puts history tab/API** (Calls has `/unusual-calls-log`) |
| 404 | GET | `/api/morning-brief` | Morning Brief tab cannot load via this path |
| 401 | GET | `/gamma-wall` | Gamma Wall unauthorized without token |
| 401 | GET | `/aiem-paper-portfolio` | Paper Money / Portfolio AIEM section unauthorized |

### 3.2 Screenshot tabs (user examples)

| Tab | API | Live status | Finding |
|-----|-----|-------------|---------|
| MICRO/SMALL CALLS | `/unusual-calls/microcap` | OK (n=3) | Working; vol/OI & OTM math correct |
| UNUSUAL PUTS | `/unusual-puts` | EMPTY (n=0) | After-hours: “Market closed — showing last logged…” but **0 hits**; no history route |
| BEAR FLOW | `/bear-flow` | OK (n=16) | Scores low → EXTREME/HIGH/MID cards = 0 (threshold math OK); score-trace field **fixed** |
| INSIDER RADAR | `/insider-radar` | OK but **n=38,823** (~18MB) | Unbounded 90d dump; UX barely usable |
| Insider Alert Log | `/insider-alerts` | OK (n=158) | Persists ≥70 scores |
| Insider Outcomes | `/insider-outcomes` | EMPTY (n=0) | Needs `earnings_date` past; rarely grades |
| EOD SWEEP | `/eod-sweeps` | OK (n=31) | Working |
| SWEEP TRACK RECORD | `/eod-sweep-track-record` | Data OK | Labels T+1/T+3/T+5 (not T+2); WR math OK; flat `0.0→null` **fixed** |
| 0DTE PAPER TRACK | `/0dte/paper-trades` + `/paper-stats` | OK (125 trades, WR 22.4%) | Stats math OK; UI stop −20% vs code −6% **fixed**; avg-loss window mismatch remains |
| HIGH CONVICTION | `/conviction-calls` | OK (n=15) | Score formula OK |
| CONVICTION TRACK | `/conviction-outcomes` | OK (n=26) | d1 settled; d3/d5 often unset until aged |

### 3.3 EMPTY / STALE (many Yahoo-backed)

Often empty or stale when Yahoo throttles / market closed:

- Market overview, Composite score, Premarket, Insider Form-4 trades  
- Sector heat/rotation, Multi-signal, Earnings calendar, Short squeeze  
- EOD accumulation (live), Squeeze setup, IV rank, ORB, Nano morning  
- Morning inflows / Cross-scanner sparse  

**Have DB fallback (stale but usable):** Unusual Calls, AI Short Calls, Daily Top10, Conviction Stack, Morning Runners, Bull Flow top10 (POST).

### 3.4 Working families (OK)

Bull Flow history, AI Trades + track log, Whale activity/log, Calls log, Far-OTM sweeps, Gamma pressure, OI buildup, HC ETFs, EOD Sweep + track, 0DTE paper, Standout track, EOD Accum track, Nano carry, Runner outcomes, Grinder, Gap+Vol, Washout, Candlestick confluence, Breakout radar (POST), Squeeze detector (POST).

---

## 4. All 81 tabs — inventory & E2E status

Status key: **OK** live data · **STALE** cached · **EMPTY** no rows · **FAIL** route/auth · **MATH** formula issue (see §5) · **UX** usable but misleading · **ORPHAN** not in dropdown

| # | Tab | id | Primary API | Storage | E2E |
|--:|-----|-----|-------------|---------|-----|
| 1 | OVERVIEW | overview | `/daily-top10`, `/bull-flow/top10`, `/market/overview` | live/cache | STALE/EMPTY mix |
| 2 | AI TRADES | aitrades | `/ai-trades` | `ai_trade_log` | OK |
| 3 | PAPER MONEY | papermoney | `/aiem-paper-portfolio` | `aiem_paper_trades` | **FAIL 401** |
| 4 | SCORE BOARD | composite | `/composite-score` | `composite_score_history` | EMPTY (paused) |
| 5 | TOP SCORE 8+ | topscore | `/conviction-stack` | stack watchlist | STALE |
| 6 | MORNING BRIEF | morningbrief | `/api/morning-brief` | cache | **FAIL 404** |
| 7 | CONVERGENCE | convergence | `/convergence` | cache | EMPTY |
| 8 | DARK POOL | darkpool | `/darkpool` | DB | OK |
| 9 | GAMMA WALL | gammawall | `/gamma-wall` | live | **FAIL 401** |
| 10 | PRE-MARKET | premarket | `/premarket` | live | EMPTY |
| 11 | BULL FLOW | bullflow | `/bull-flow/top10` POST | signals | OK (stored) |
| 12 | PERSISTENCE | persistence | `/bull-flow/persistence` | calc | (not re-probed) |
| 13 | SMART MONEY | smartmoney | `/smart-money/scan` | cache | (manual) |
| 14 | CONGRESS | congress | `/congress/trades` | cache | (not re-probed) |
| 15 | STOCK LOOKUP | lookup | `/stock/analyze` | ephemeral | on-demand |
| 16 | SCANNER | scanner | `/stock/scan` | live | on-demand |
| 17 | OUTCOMES | outcomes | `/outcomes` | `signal_outcomes` | OK |
| 18 | ANALYTICS | analytics | `/analytics/historical` POST | on-demand | ERR (no data) |
| 19 | PROP DESK | propdesk | `/prop/*` | in-process | paper sim |
| 20 | SQUEEZE | squeeze | `/squeeze/detector` POST | live | OK |
| 21 | BREAKOUT | breakout | `/breakout/radar` POST | live | OK |
| 22 | INSIDERS | insiders | `/insider/trades` | Form-4 | EMPTY (Yahoo) |
| 23 | MARKET | market | `/market/overview` | live | EMPTY (paused) |
| 24 | PORTFOLIO | portfolio | `/portfolio` + AIEM | mixed | AIEM 401 |
| 25 | AI TRACK RECORD | trackrecord | `/ai-trade-log` | `ai_trade_log` | OK |
| 26 | WHALE ACTIVITY | whale | `/whale-activity` | `whale_blocks` | OK |
| 27 | WHALE LOG | whalelog | `/whale-history` | `whale_blocks` | OK |
| 28 | MY WATCHLIST | watchlist | `/trade-watchlist` | `trade_watchlist` | EMPTY (user) |
| 29 | INSIDER RADAR | insiderradar | `/insider-radar` (+alerts/outcomes) | `insider_alerts` | **UX** (38k) / outcomes EMPTY |
| 30 | UNUSUAL CALLS | unusualcalls | `/unusual-calls` | `unusual_calls_log` | STALE OK |
| 31 | UNUSUAL PUTS | unusualputs | `/unusual-puts` | `unusual_puts_log` (write-only) | **EMPTY + no log API** |
| 32 | BEAR FLOW | bearflow | `/bear-flow` | live composite | OK / UX cards |
| 33 | CALLS LOG | unusualcallslog | `/unusual-calls-log` | `unusual_calls_log` | OK |
| 34 | SMART MONEY PRESSURE | smpressure | `/conviction-stack` | stack | STALE |
| 35 | 8-LAYER CONVICTION | convictionstack | `/conviction-stack` | watchlist | STALE |
| 36 | SWEEP RADAR | sweepradar | `/far-otm-sweeps` | live | OK |
| 37 | SECTOR HEAT | sectorheat | `/sector-heat` | live | EMPTY |
| 38 | GAMMA SQUEEZE | gammapressure | `/gamma-pressure` | alerts | OK |
| 39 | OI BUILDUP | oiaccum | `/oi-accumulation` | snapshots | OK |
| 40 | HC ETFs | etfcalls | `/etf-calls` | unusual subset | OK |
| 41 | HIGH CONVICTION | convictioncalls | `/conviction-calls` | snapshots | OK |
| 42 | EOD SWEEP | eodsweep | `/eod-sweeps` | `eod_sweep_log` | OK |
| 43 | SWEEP TRACK RECORD | sweeptrack | `/eod-sweep-track-record` | `eod_sweep_log` | OK + **MATH fix** |
| 44 | 0DTE PAPER TRACK | 0dte-paper | `/0dte/paper-*` | `paper_0dte_trades` | OK + **UI fix** |
| 45 | CONVICTION TRACK | convictiontrack | `/conviction-outcomes` | outcomes | OK |
| 46 | MY TRADES | mytrades | `/my-trades` | `my_trades` | EMPTY (user) |
| 47 | AI EARLY MOVERS | aiearlymovers | `/ai-early-movers` | isolated | OK (experimental) |
| 48 | AI SHORT CALLS | aishortcalls | `/ai-short-calls` | log | STALE |
| 49 | SHORT CALLS RECORD | shortcallrecord | `/ai-short-calls-log` | log | OK |
| 50 | NET FLOW | netflow | `/net-flow` | live | OK |
| 51 | MICRO NET FLOW | micronetflow | `/net-flow/microcap` POST | cache | EMPTY note (closed) |
| 52 | MICRO/SMALL CALLS | microcalls | `/unusual-calls/microcap` | microcap log | OK |
| 53 | MID NET FLOW | midnetflow | microcap mid slice | same | (closed) |
| 54 | FLOW STREAK | streakflow | `/net-flow/multiday` | multi | OK |
| 55 | DOUBLE SIGNAL | crossscanner | `/cross-scanner` | overlap | EMPTY |
| 56 | STANDOUT FLOW | standoutflow | `/morning-inflows` | scan_history | EMPTY |
| 57 | STANDOUT TRACK | standouttrack | `/standout-track` | history⋈outcomes | OK |
| 58 | MORNING RUNNERS | morningrunners | `/morning-runners` | live | STALE |
| 59 | EOD ACCUM | eodaccum | `/eod-accumulation` | picks | EMPTY (Yahoo) |
| 60 | EOD TRACK | eodaccumtrack | `/eod-accum-track` | outcomes | OK |
| 61 | SQUEEZE SETUP | squeezesetup | `/squeeze-setup` | live | EMPTY (closed) |
| 62 | 52WK BREAKOUT | breakout52week | `/52week-breakout` | live | OK |
| 63 | SECTOR ROTATION | sectorrotation | `/sector-rotation` | live | EMPTY |
| 64 | MULTI-SIGNAL | multisignal | `/multi-signal` | DB+localStorage | EMPTY (Yahoo) |
| 65 | IV RANK | ivrank | `/iv-rank` | live | EMPTY |
| 66 | MARKET PRESS | marketpress | `/market-press` | news | EMPTY-ish |
| 67 | EARNINGS CALENDAR | earningscal | `/earnings-calendar` | calendar | EMPTY (Yahoo) |
| 68 | SQUEEZE RADAR | squeezeradar | `/short-squeeze` | SI | EMPTY |
| 69 | NANO MORNING | nanomorning | `/nano-morning/candidates` | nano | EMPTY |
| 70 | S1B·S1C·S1D | nanocarry | `/nano-carry/picks` | carry | OK |
| 71 | CONVICTION SCORE | ics | client checklist | none | OK (offline) |
| 72 | MULTI-DAY RUNNER | multidayrunner | `/multiday-runners` | live | EMPTY payload |
| 73 | RUNNER OUTCOMES | runneroutcomes | `/runner-outcomes` | outcomes | OK |
| 74 | STEADY GRINDERS | steadygrinder | `/grinder-scan` | live | OK |
| 75 | GAP+VOL SIGNAL | gapvolume | `/gap-volume-signal` | live | OK |
| 76 | ORB BREAKOUT | orb | `/orb-signals` | live | EMPTY |
| 77 | WASHOUT COMPLETE | washout-complete | `/momentum-washout-complete` | live | OK |
| 78 | CANDLESTICK CONFLUENCE | candlestick-confluence | `/candlestick-confluence` | patterns | OK |
| 79 | QUANT AGENT | quantagent | `/aiem/chat/*` | chat+BYOK | gated |
| 80 | GAS BOARD | gasboard | `/user/gas-board` | prefs | gated |
| 81 | SIGNAL INTEL | signalintel | `/signal-intelligence` | catalog | EMPTY-ish |
| — | *(orphan)* Backtest | backtest | `/backtest` | — | **ORPHAN** |
| — | *(orphan)* Alerts | alerts | `/alerts` | — | **ORPHAN** |

---

## 5. Math & formula audit (summary)

Full table: `stockscanner-indicator-pattern-math-audit.md`.

### Fixed in this PR

| Bug | Fix |
|-----|-----|
| AEIM v3 MACD stub (`signal=0`, `hist=0.9×macd`) | Real EMA9 signal + hist = macd − signal |
| Live MFI signed by close Δ | Sign by typical-price Δ |
| Sweep Track `float(x) if x` nulls flats | `is not None` |
| 0DTE UI stop −20% vs code −6% | UI + comments → −6% |
| Bear Flow UI `pct_change` (missing) | Use `close_strength` |

### Still open (need approval / follow-up)

| Issue | Why |
|-------|-----|
| `unusual_puts_log` UNIQUE + `/unusual-puts-log` | Schema change (standing rule: paste diff + Joel OK before migrate) |
| 0DTE stats all-time vs days-filtered trade list | Window mismatch → avg loss card can disagree with visible rows |
| Insider Radar unbounded query | Cap/paginate 90d `$10k+` dump |
| AEIM v3 RSI = SMA not Wilder; BB %B scale split | Consistency cleanup |
| Win rate treats flat `0%` as loss | Policy — document or exclude |

### Confirmed CORRECT (high-traffic)

RSI/MACD/BB/ATR/ADX/Stoch/VWAP (main paths), Vol/OI, OTM%, premium, High Conviction score, Bear Flow 30/25/25/20 weights, EOD Sweep T+1/T+3/T+5 WR (graded `n`), candlesticks, ORB rule, 0DTE +50%/−6% exits in code, HC outcomes d1 math.

---

## 6. Persistence map (scan → store → history UI)

| Family | Scan / write | Table | History UI |
|--------|--------------|-------|------------|
| Unusual Calls | scheduled + `/unusual-calls` | `unusual_calls_log` | CALLS LOG ✅ |
| Micro/Small Calls | scheduled microcap | `unusual_calls_microcap_log` | MICRO/SMALL CALLS ✅ |
| Unusual Puts | `/unusual-puts` side-effect insert | `unusual_puts_log` | **No GET log route ❌** |
| EOD Sweep | 16:20 auto-log | `eod_sweep_log` | SWEEP TRACK ✅ |
| High Conviction | 16:25 snapshot | `conviction_calls_*` | CONVICTION TRACK ✅ |
| 0DTE Paper | `zero_dte_paper_monitor` / EOD | `paper_0dte_trades` | 0DTE PAPER TRACK ✅ |
| Insider Radar ≥70 | bg scan auto-save | `insider_alerts` | Alert Log ✅ / Outcomes ⚠️ |
| AI Trades | 10:00 auto | `ai_trade_log` | AI TRACK RECORD ✅ |
| AI Short Calls | 10:15 auto | `ai_short_calls_log` | SHORT CALLS RECORD ✅ |
| Whales | scan | `whale_blocks` | WHALE LOG ✅ |
| Standout / EOD Accum | scans | `scan_history` / `eod_accum_*` | track tabs ✅ |
| My Trades / Watchlist | user CRUD | `my_trades` / `trade_watchlist` | user-empty until used |

**Root cause for “history not storing for a month” on Puts:** inserts may run, but there is **no UI/API to read** them; `ON CONFLICT DO NOTHING` is also a no-op (no unique key) so dedupe never works.

---

## 7. Schedulers (StockScanner-relevant)

Registered in `main.py` (America/New_York). Selected feeders:

| Job id | When (ET) | Feeds |
|--------|-----------|-------|
| `market_open_unusual_calls` … `eod_unusual_calls` | 9:36–16:00 | Unusual Calls / log |
| microcap call slots (`_mc_*`) | intraday | MICRO/SMALL CALLS |
| `eod_sweep_auto_log` | 16:20 | EOD Sweep + Track |
| `eod_sweep_outcomes` | 16:35 | Sweep T+N grading |
| `conviction_snapshot` / `conviction_outcomes` | 16:25 / 16:32 | High Conviction |
| `zero_dte_sweep` / `zero_dte_paper_monitor` / `zero_dte_paper_eod` | interval + 15:35 | 0DTE Paper |
| `gamma_pressure_scan` | 9–15 /10m | Gamma Squeeze |
| `oi_snapshot_*` | 8:40 / 16:30 | OI Buildup |
| `insider_outcomes_check` | 16:37 | Insider Outcomes |
| `ai_trades_auto` / `ai_short_calls_auto` / `ai_early_movers` | ~10:00–10:20 | AI tabs |
| `morning_scan` / `premarket_scan` / `eod_scan` | 9:00 / 10:32 / 16:15 | Overview / runners |
| `grinder_eod_scan` | 8:30 | Steady Grinders |
| nano / SC morning ranking | 8:00–8:15 | Nano tabs |
| `aiem_paper_*` | 9:42+ | Paper Money (auth required to view) |

Full list is large (~100+ including AIEM research/backtest jobs). Ops risk: job exists in code ≠ proved running on the GCE/Replit deploy until heartbeat checked.

---

## 8. Priority fix backlog

### P0 — user-visible broken

1. **Add `/stock-api/unusual-puts-log`** + Puts Log UI (mirror Calls Log).  
2. **Approve schema:** unique key on `unusual_puts_log` (after dedupe).  
3. **Authorize or remove gate** on Gamma Wall / Paper Money for the public scanner, or document login.  
4. **Fix Morning Brief path** (`/api/morning-brief` 404 → correct `/stock-api/...` or proxy).  
5. **Insider Radar:** `LIMIT` + pagination; don’t ship 18MB JSON.

### P1 — correctness / trust

6. Ship math fixes in this PR (MACD, MFI, sweep nulls, 0DTE −6%, Bear Flow field).  
7. Align 0DTE `/paper-stats` with `?days=` filter.  
8. Document Sweep Track: WR uses graded `n`, not “signals logged.”

### P2 — after-hours / Yahoo resilience

9. DB snapshot fallback for Multi-signal, Earnings, Short squeeze, EOD Accum live (same pattern as Unusual Calls).  
10. Remove or hide orphan Backtest/Alerts components, or add to nav.

---

## 9. How this audit was produced

```bash
# Live probe (saved)
python3 … → docs/verification/stockscanner-e2e-live-probe.json

# Math + formula review
# docs/verification/stockscanner-indicator-pattern-math-audit.md

# Code inventory
# Dashboard.tsx TABS (81) + main.py @app.route + APScheduler add_job
```

No production schema migrations were applied. Math/UI fixes are in the same PR as this report.

---

## 10. Sign-off checklist

| Question | Answer |
|----------|--------|
| Is the whole site offline? | **No** |
| Are “many tabs not working”? | **Yes — mix of FAIL routes, empty Yahoo feeds, and missing Puts history** |
| Is High Conviction math wrong? | **No** (formula OK; outcomes age naturally) |
| Is EOD Sweep / Track math wrong? | **Mostly no**; flat-return null bug fixed; UI is T+1/T+3/T+5 |
| Is 0DTE paper broken? | **Recording works**; stop % display was wrong (−20% vs −6%) — fixed |
| What’s been broken ~1 month (Puts)? | **No history API/UI** + ineffective upsert |
| Safe to promote all tabs as “live accurate”? | **No** — clear P0 list above first |
