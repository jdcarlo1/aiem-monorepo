# StockScanner AI — Complete End-to-End Audit (2026-08-05)

**Site:** https://nclexai.org (StockScanner AI)  
**All-81 matrix:** `docs/verification/stockscanner-all-81-tabs-status.json`  
**Math detail:** `docs/verification/stockscanner-indicator-pattern-math-audit.md`  
**PR:** `cursor/stockscanner-tabs-audit-5ace`

---

## Did we check every tab?

**Yes — all 81 dropdown tabs.** Each tab’s primary `/stock-api` endpoint(s) were live-probed (83 route calls) on 2026-08-05.

| Checked | Not checked |
|---------|-------------|
| Every `TABS` id in `Dashboard.tsx` (81/81) | Full mobile click-through screenshot of each screen |
| Primary API(s) per tab | Flows that require user BYOK secrets |
| Persistence routes where they exist | “Should this Yahoo tab be full after hours?” judgment per feed |

This is an **API/functional** audit of every tab, not 81 manual UI walkthroughs.

---

## Results — all 81 tabs

| Status | Count | Meaning |
|--------|------:|---------|
| **OK** | 41 | Primary API returns usable data |
| **STALE** | 15 | Data returned but stale / Yahoo throttle / closed-cache |
| **EMPTY** | 15 | 200 with zero rows |
| **FAIL** | 7 | 401/404/broken — tab cannot function |
| **ERR** | 1 | API error body |
| **DEGRADED** | 1 | Insider Radar (38k rows, empty outcomes) |
| **GATED** | 1 | Quant Agent (BYOK) |

**41 healthy · 15 stale · 15 empty · 7 fail · 1 err · 1 degraded · 1 gated**

### FAIL (broken)

| Tab | Cause |
|-----|-------|
| Unusual Puts | Empty + `/unusual-puts-log` **404** (no history API) |
| Paper Money | `/aiem-paper-portfolio` **401** |
| Portfolio (AIEM) | same **401** |
| Gamma Wall | `/gamma-wall` **401** |
| Morning Brief | `/api/morning-brief` **404** |
| Gas Board | `/user/gas-board` fail |
| Stock Lookup | `/stock/analyze` probe fail |

### EMPTY (code path OK, no data now)

Score Board, Convergence, Persistence, Congress, Scanner, Prop Desk, Insiders, My Watchlist, My Trades, EOD Accum (live), Squeeze Setup, Multi-Signal, Squeeze Radar, Nano Morning, ORB.

### STALE (showing cached/throttled data)

Overview, Top Score 8+, Premarket, Market, Unusual Calls, SM Pressure, 8-Layer Conviction, AI Short Calls, Micro/Mid Net Flow, Double Signal, Standout Flow, 52wk Breakout, IV Rank, Earnings Calendar.

### OK (41 working)

AI Trades, Dark Pool, Bull Flow, Smart Money, Outcomes, Squeeze, Breakout, AI Track Record, Whale Activity/Log, Bear Flow, Calls Log, Sweep Radar, Sector Heat, Gamma Squeeze, OI Buildup, HC ETFs, High Conviction, EOD Sweep, Sweep Track, 0DTE Paper, Conviction Track, AI Early Movers, Short Calls Record, Net Flow, Micro/Small Calls, Flow Streak, Standout Track, Morning Runners, EOD Track, Sector Rotation, Market Press, Nano Carry, Conviction Score, Multi-Day Runner, Runner Outcomes, Steady Grinders, Gap+Vol, Washout, Candlestick Confluence, Signal Intel.

---

## Your screenshot examples

| Tab | Status |
|-----|--------|
| MICRO/SMALL CALLS | OK |
| UNUSUAL PUTS | **FAIL** (no history endpoint) |
| BEAR FLOW | OK (cards 0 when scores &lt;45) |
| INSIDER RADAR | **DEGRADED** (~38k rows) |
| EOD SWEEP / TRACK | OK (math OK; flat-null fixed) |
| 0DTE PAPER | OK (125 trades, 22.4% WR; stop UI fixed to −6%) |
| HIGH CONVICTION | OK |

---

## Math fixes in this PR

1. AEIM v3 MACD — real EMA9 signal  
2. Live MFI — typical-price sign  
3. Sweep Track — keep `0.0` flats  
4. 0DTE UI stop −20% → −6%  
5. Bear Flow score-trace → `close_strength`

**Needs your OK:** unique key on `unusual_puts_log` + `/unusual-puts-log` GET (schema — not applied).

---

## P0 backlog

1. Puts history API + UI  
2. Auth story for Paper Money / Gamma Wall  
3. Morning Brief 404  
4. Insider Radar pagination  
5. Yahoo-empty tabs: DB snapshot fallback  

Full machine matrix: `stockscanner-all-81-tabs-status.json`.
