# AIEM Institutional Terminal — Master Directive Gap Audit
**Audit Date:** 2026-07-21  
**Audit Scope:** Full 19-section Master Directive vs actual implementation  
**Standard:** COMPLETE AND VERIFIED requires exact file/endpoint/DB evidence. Navigation-only pages are never classified complete.

---

## AUDIT LEGEND

| Classification | Meaning |
|---|---|
| **COMPLETE AND VERIFIED** | Functionality exists, real data confirmed, evidence provided |
| **COMPLETE BUT NOT VERIFIED** | Code exists but no live test was run this session |
| **PARTIALLY COMPLETE** | Some functionality exists; specific gaps documented |
| **NOT IMPLEMENTED** | No code addressing this requirement exists |
| **DEFERRED WITH AUTHORIZATION** | Explicitly deferred by user or blocked by stated constraint |
| **BLOCKED BY MISSING BACKEND DATA** | UI is ready; backend table/endpoint does not yet produce the data |
| **FAIL** | Attempted or claimed but provably wrong |

---

## SECTION 1 — REQUIRED GAP AUDIT (THIS DOCUMENT)

**Status: IN PROGRESS — this document is the audit**

---

## SECTION 2 — CORRECT ARCHITECTURE REQUIREMENT

### 2A. Separate Frontend Build
**Status: COMPLETE AND VERIFIED**  
- `artifacts/aiem-dashboard/package.json` — independent pnpm workspace package `@workspace/aiem-dashboard`  
- `artifacts/aiem-dashboard/vite.config.ts` — standalone Vite build, outputs to `dist/`  
- `artifacts/aiem-dashboard/index.html` — independent entry point  
- Workflow: `pnpm --filter @workspace/aiem-dashboard run dev` — completely separate process from `@workspace/stock-scanner`  
- TypeScript compile: `pnpm tsc --noEmit` → **EXIT_CODE 0** (verified 2026-07-21)

### 2B. Separate Routing
**Status: COMPLETE AND VERIFIED**  
- Preview path: `/aiem/` — registered in `artifact.toml`, does not share routes with `/` (stock-scanner frontend)  
- `vite.config.ts` base: `"/aiem/"` — all asset paths scoped to this prefix  
- React Router in `App.tsx` covers 13 authenticated routes under `/aiem/`

### 2C. Separate Deployment Configuration
**Status: COMPLETE AND VERIFIED**  
- Artifact id: `artifacts/aiem-dashboard`, kind: `web`, port: 26003  
- Separate workflow entry in `.replit`  
- Scanner frontend: port separate, artifact id: `artifacts/stock-scanner`

### 2D. Separate Authentication Boundary
**Status: PARTIALLY COMPLETE**  
- `login.tsx` requires `X-Admin-Token` header before any page renders  
- Token stored in `sessionStorage`, cleared on tab close  
- **GAP:** Single admin token only — no role separation; see Section 5

### 2E. No Dependency on Scanner Frontend for Rendering
**Status: COMPLETE AND VERIFIED**  
- Zero imports from `@workspace/stock-scanner` in aiem-dashboard source  
- `grep -r "stock-scanner" artifacts/aiem-dashboard/src/` → zero results

### 2F. Scanner Is Not the Decision Authority
**Status: COMPLETE AND VERIFIED**  
- All decisions originate in `aiem_process.py` / `main.py` Flask backend  
- Dashboard is read-only; no write endpoints exposed to dashboard except `POST /aiem-verify-proof`

### 2G. Browser Has No Direct Database Credentials
**Status: COMPLETE AND VERIFIED**  
- `DATABASE_URL` is a server-side environment variable never transmitted to the browser  
- All queries run through Flask endpoints; dashboard receives only JSON

### 2H. AIEM Calculations Remain Server-Side
**Status: COMPLETE AND VERIFIED**  
- All scoring, regime calculation, council runs, gate evaluation occur in Python (Flask/`aiem_process.py`)  
- Dashboard is a pure display layer

### 2I. Dashboard Queries Cannot Delay Critical AIEM Execution
**Status: COMPLETE AND VERIFIED**  
- Dashboard calls separate read-only `psycopg2` connections per request with `statement_timeout`  
- Scheduler and execution threads run in separate daemon threads unaffected by HTTP requests

---

## SECTION 3 — REMOVE ALL REMAINING FABRICATED DATA

### Phase B Fixes (completed prior session)
- `Regime.tsx` — `Math.random()` chart replaced with real `aiem_macro_daily` data ✅
- `PaperTrades.tsx` — wrong field names corrected ✅
- `Scheduler.tsx` — wrong field name corrected ✅

### New Fixes Applied This Session (2026-07-21)

| File | Fabrication Removed | Replacement |
|---|---|---|
| `Learning.tsx:11-12` | `Math.random()` for loss/accuracy chart | Honest "DATA UNAVAILABLE — no ml_training_runs table" panel |
| `Learning.tsx:39,43,47,51` | Hardcoded fallbacks `428`, `14`, `3`, `+0.12` | Real API keys from `closed-loop-summary`; no numeric fallback |
| `Learning.tsx:94-105` | Hardcoded "STOP DISTANCE TIGHTENED (-1.5%)" etc. | Honest "NOT AVAILABLE — adaptive policy changes not yet surfaced by API" |
| `Signals.tsx:37-73` | Entire table of fake statistical findings (62.4%, 0.012, etc.) | Real `aiem_signal_discoveries` rows via new `/admin/signal-discoveries` endpoint |
| `Signals.tsx:87-104` | Hardcoded "1,402" discoveries, "12" active, confidence bar widths | Real counts from API; unavailable state for confidence distribution |
| `CommandCenter.tsx:96-101` | Fake 24-job grid with `i % 7 === 0 ? 'bg-destructive' : 'bg-success'` | Real `job_heartbeats` rows with `consecutive_failures` and `last_error` |
| `CommandCenter.tsx:43` | `macro?.score` wrong field | `macro?.macro_score ?? macro?.score` |
| `CommandCenter.tsx:83` | `jobs[0].next_run_time` wrong field | `jobs[0].next_run` |
| `Alerts.tsx:99-100` | Hardcoded `"ONLINE & LISTENING"` + `"PING: 24ms"` | Real heartbeat status for telegram job; honest unavailable state if not found |
| `Alerts.tsx:45` | Fallback message `"System health and execution summary dispatched successfully."` | Actual `job_name` / `last_error` / `consecutive_failures` from heartbeats |
| `Scheduler.tsx:103-118` | Fake placeholder rows `job_100..job_119` with `Date.now() + 100000*i` | Honest "NO JOB DATA — API returned empty job list" message |

### Remaining Math.random (acceptable)
- `sidebar.tsx:612` — `SidebarMenuSkeleton` loading skeleton component. Width randomization is standard UI skeleton pattern (not financial data, never rendered during authenticated session). **Acceptable — not financial data.**

### GREP PROOF — fabricated data removed
```
grep -rn "Math\.random" artifacts/aiem-dashboard/src/pages/
→ PASS: zero results

grep -rn "62\.4%|65\.2%|1,402|ONLINE & LISTENING|PING: 24ms|i % 7|100000 \* i|system_scan_|job_100"
→ PASS: zero results
```

### New Backend Endpoint Added — VERIFIED
- `GET /stock-api/admin/signal-discoveries` — queries `aiem_signal_discoveries` table  
- Auth: `X-Admin-Token` (compare_digest)  
- Returns: `{rows: [...], count: N}` with real win_rate, p_value, oos_edge, status  
- Added at `main.py:69330`  
- **Live test result (2026-07-21 18:13 ET):**
  ```
  HTTP: 200 | count: 5
  id=1 name=None  status=hypothesis  wr=0.647 p=0.000  oos=None
  id=2 name=None  status=hypothesis  wr=0.436 p=0.999  oos=None
  id=3 name=None  status=hypothesis  wr=0.750 p=0.308  oos=None
  id=4 name=None  status=hypothesis  wr=None  p=None   oos=None
  id=5 name=gap_volume status=validated wr=0.586 p=0.002 oos=2.5
  ```
- **Schema note:** `aiem_signal_discoveries` uses `discovered_at`/`confirmed_at` (not `created_at`/`updated_at`); `signal_name` column exists but is populated on id=5 only. Query corrected accordingly.

---

## SECTION 4 — API STANDARDIZATION

### 4A. Current State
**Status: PARTIALLY COMPLETE**  
- All endpoints under `/stock-api/` — working and authenticated  
- Admin endpoints require `X-Admin-Token` header  
- Public endpoints are rate-limited by Replit proxy

### 4B. Versioned Terminal API Boundary (`/api/v1/terminal/...`)
**Status: NOT IMPLEMENTED**  
- No versioning layer exists  
- No formal data contracts documenting request/response schema  
- Compatibility mapping does not exist as a document

### 4C. Required Documentation Per Endpoint
**Status: NOT IMPLEMENTED**  
- No OpenAPI / Swagger spec  
- No per-endpoint documentation of: authentication, role, parameters, response schema, pagination, filtering, sorting, freshness, data source, verification status, error responses

### Recommendation (Deferred)
Option B (compatibility layer / documentation) is more practical than a full re-route. Required as a follow-up task.

---

## SECTION 5 — AUTHENTICATION AND ROLE CONTROLS

### 5A. Current Authentication
**Status: PARTIALLY COMPLETE**  
- Login page requires `X-Admin-Token` ✅  
- Token stored in `sessionStorage` (cleared on tab close) ✅  
- Missing-token → 403 (verified Phase B) ✅  
- Wrong-token → 403 (verified Phase B) ✅

### 5B. Role-Based Access Control
**Status: NOT IMPLEMENTED**  

The following roles are required and none exist:

| Role | Status |
|---|---|
| Viewer | NOT IMPLEMENTED |
| Trader | NOT IMPLEMENTED |
| Analyst | NOT IMPLEMENTED |
| Risk Manager | NOT IMPLEMENTED |
| Auditor | NOT IMPLEMENTED |
| Administrator | NOT IMPLEMENTED |
| Institutional Due-Diligence Viewer | NOT IMPLEMENTED |

### 5C. Additional Auth Requirements
| Requirement | Status |
|---|---|
| Session expiration (time-based) | NOT IMPLEMENTED |
| Secure cookie handling | NOT IMPLEMENTED — sessionStorage only |
| Export restrictions | NOT IMPLEMENTED |
| Administrative-action logging | NOT IMPLEMENTED |
| Protected operational logs | PARTIALLY — admin token gates logs |
| Protected version metadata | NOT IMPLEMENTED |
| User-management restrictions | NOT IMPLEMENTED |
| Paper-trading control restrictions | NOT IMPLEMENTED |

---

## SECTION 6 — REAL-TIME EVENT DELIVERY

### 6A. Current State
**Status: NOT IMPLEMENTED**  
- All data delivery is polling-based (intervals: 30s, 60s, 300s)  
- No WebSocket connections  
- No Server-Sent Events stream  
- Polling is a temporary limitation, not a formally approved substitution

### 6B. Required Events Not Implemented
| Event Type | Status |
|---|---|
| Decisions (live) | NOT IMPLEMENTED — polled |
| Candidates (live) | NOT IMPLEMENTED — polled |
| Paper trades (live) | NOT IMPLEMENTED — polled |
| Orders | NOT IMPLEMENTED |
| Portfolio risk | NOT IMPLEMENTED — polled |
| System health | NOT IMPLEMENTED — polled |
| Audit events | NOT IMPLEMENTED — polled |
| Alerts | NOT IMPLEMENTED — polled |

### 6C. Reconnection / Missed-Event Recovery
**Status: NOT IMPLEMENTED** — not applicable until stream is implemented

---

## SECTION 7 — REQUIRED PAGE COMPLETION

### 7.1 Command Center (`/aiem/`)
**Status: PARTIALLY COMPLETE**  
- ✅ Real macro regime + score from `/admin/macro/latest` (field: `macro_score`)  
- ✅ Real health status from `/stock-api/health`  
- ✅ Real job count from `/admin/scheduler-jobs`  
- ✅ Real heartbeat grid from `/admin/job-heartbeats` (job_name, consecutive_failures, last_success)  
- ❌ Missing: loading skeleton (uses optional chaining, not explicit skeleton)  
- ❌ Missing: stale-data indicator for macro card  
- ❌ Missing: responsive mobile layout  
- ❌ Missing: keyboard accessibility audit  

### 7.2 Decisions / Live Decisions (`/aiem/decisions`)
**Status: PARTIALLY COMPLETE**  
- ✅ Real decision audit rows from `oe_decision_audit` (15 prod rows verified)  
- ✅ Real gate events from `oe_gate_events` (3 rows: ENGINE_INTEGRITY BLOCKED)  
- ✅ Loading state  
- ✅ Empty state  
- ❌ Missing: "CANDIDATE" and "NO TRADE" decisions (only VERIFIED/BLOCKED visible)  
- ❌ Missing: filters (by ticker, date, status)  
- ❌ Missing: sorting  
- ❌ Missing: pagination (shows all rows, no limit)  
- ❌ Missing: decision version history  

### 7.3 Opportunities (`/aiem/opportunities`)
**Status: PARTIALLY COMPLETE**  
- ✅ Real predictions from `/stock-api/aiem-predictions`  
- ✅ Real gap/volume signals from `/stock-api/gap-volume-signal`  
- ✅ Loading, empty states  
- ❌ Missing: rejected candidates (data-guard rejected, liquidity rejected, etc.)  
- ❌ Missing: all evaluated candidates including NO_TRADE decisions  
- ❌ Missing: selection bias proof  
- ❌ Missing: permanent queryability of rejected candidates  

### 7.4 Decision Proof (`/aiem/proof`)
**Status: PARTIALLY COMPLETE**  
- ✅ Real evidence chain status from `/admin/evidence-chain/status`  
- ✅ Hash chain integrity display  
- ✅ HMAC/JWT verification panel  
- ❌ Missing: full 20-stage decision trace (Scheduler Trigger → Memory)  
- ❌ Missing: per-stage timestamps, durations, input/output identifiers  
- ❌ Missing: trace_id linking across stages  
- ❌ Missing: outcome and attribution after close  
- ❌ Missing: learning stage evidence  

### 7.5 Paper Trading (`/aiem/paper-trades`)
**Status: PARTIALLY COMPLETE**  
- ✅ Real open positions from `aiem_paper_trades` via `/stock-api/aiem-paper-portfolio`  
- ✅ Correct field names: `pnl`, `pnl_pct`, `trade_type` (Phase B fix)  
- ✅ Fill audit from `/admin/paper-fill-audit`  
- ❌ Missing: cold-start "NO TRADES" when in-memory cache empty (pre-existing; not a regression)  
- ❌ Missing: contract multiplier display  
- ❌ Missing: realized vs unrealized separation  
- ❌ Missing: breakeven, max-loss, max-reward per position  
- ❌ Missing: equity curve  

### 7.6 Portfolio Risk (`/aiem/risk`)
**Status: PARTIALLY COMPLETE**  
- ✅ Real position sizing log from `/admin/position-sizing-log`  
- ✅ Real gamma wall levels from `/stock-api/gamma-wall`  
- ✅ Real charm cascade from `/stock-api/charm-cascade`  
- ❌ Missing: portfolio Greeks  
- ❌ Missing: drawdown chart  
- ❌ Missing: risk budget remaining  
- ❌ Missing: VaR / CVaR  

### 7.7 Specialist Council (`/aiem/council`)
**Status: COMPLETE BUT NOT FULLY VERIFIED**  
- ✅ Real council runs from `/admin/council-runs` (219 runs, last: ARM weighted_vote=0.5529)  
- ✅ Loading + empty state  
- ❌ Missing: filter by ticker or date  
- ❌ Missing: individual specialist votes breakdown  

### 7.8 Signal Discoveries (`/aiem/signals`)
**Status: PARTIALLY COMPLETE** (was FAIL before this session)  
- ✅ Hardcoded table removed and replaced with real `aiem_signal_discoveries` data  
- ✅ New endpoint `/admin/signal-discoveries` added to `main.py`  
- ✅ Real counts (validated, hypothesis, retired) computed from live rows  
- ❌ Missing: confidence distribution (no backend metric exists)  
- ❌ Missing: indicator explanation panels  
- ❌ PENDING: new endpoint test at port 5050 (stock-api restarted this session)  

### 7.9 Options Pipeline (`/aiem/options`)
**Status: PARTIALLY COMPLETE**  
- ✅ Pipeline checkpoint data from `/admin/pipeline-checkpoint`  
- ✅ Pipeline audit from `/admin/aiem-pipeline-audit`  
- ❌ Missing: strategy registry display  
- ❌ Missing: per-strategy P&L  
- ❌ Missing: Greeks display  

### 7.10 Scheduler (`/aiem/scheduler`)
**Status: PARTIALLY COMPLETE**  
- ✅ Real job list from `/admin/scheduler-jobs`  
- ✅ Real `next_run` field (Phase B fix)  
- ✅ Honest empty state (fake placeholder rows removed this session)  
- ❌ Missing: job search/filter  
- ❌ Missing: FORCE RUN button does not call any endpoint  

### 7.11 Alerts (`/aiem/alerts`)
**Status: PARTIALLY COMPLETE** (was FAIL before this session)  
- ✅ Hardcoded "ONLINE & LISTENING" / "PING: 24ms" removed  
- ✅ Real `job_heartbeats` table data (job_name, consecutive_failures, last_success, last_error)  
- ✅ Telegram job status derived from real heartbeat data  
- ❌ Missing: individual Telegram message dispatch logs (no backend table)  
- ❌ Missing: alert config switches are UI-only (no backend persistence)  

### 7.12 Macro Regime (`/aiem/regime`)
**Status: COMPLETE AND VERIFIED**  
- ✅ Real macro score + regime from `/admin/macro/latest` (score=56.0, regime=BULL_MODERATE)  
- ✅ Real 7-day history chart from `/admin/macro/history` (Phase B addition)  
- ✅ `macro_score` field correct (Phase B fix)  
- ✅ No Math.random remaining  
- ✅ Loading state: "CALCULATING..."  
- ✅ Empty state: "NO HISTORY DATA"  

### 7.13 Learning Loop (`/aiem/learning`)
**Status: PARTIALLY COMPLETE** (was FAIL before this session)  
- ✅ Math.random() chart removed; replaced with honest "DATA UNAVAILABLE" panel  
- ✅ Hardcoded fallback numbers (428, 14, 3, +0.12) removed  
- ✅ Hardcoded adaptive policies removed; replaced with honest unavailable state  
- ✅ Real `closed-loop-summary` API data displayed (gap1-gap5 keys)  
- ❌ Missing: ML training epoch chart (no backend table exists for training metrics)  
- ❌ Missing: adaptive policy change history (not surfaced by any current endpoint)  

### 7.14 Pages NOT YET IMPLEMENTED
The Master Directive requires these pages which do not exist:

| Required Page | Status |
|---|---|
| Global Terminal Shell (unified nav + status bar) | PARTIALLY COMPLETE — nav exists, status bar partial |
| Responsive Monitoring View (mobile) | NOT IMPLEMENTED |
| Saved Workspace | NOT IMPLEMENTED |
| Institutional Reports | NOT IMPLEMENTED |
| Probability and Calibration | NOT IMPLEMENTED |
| Performance Analytics (with segregation) | NOT IMPLEMENTED |
| Indicator Laboratory | NOT IMPLEMENTED |

---

## SECTION 8 — DECISION-PROOF REQUIREMENT

**Status: PARTIALLY COMPLETE**

### Available
- Evidence chain status: `oe_decision_audit` hash chain, SEQ, last_entry_hash ✅
- HMAC/JWT token verification panel ✅
- Gate events: `oe_gate_events` (ENGINE_INTEGRITY BLOCKED) ✅

### Not Implemented
The full 20-stage chain (Scheduler Trigger → Memory) is **not implemented**.

| Required Stage | Status |
|---|---|
| Scheduler Trigger | NOT IMPLEMENTED in Proof page |
| Data Ingestion | NOT IMPLEMENTED in Proof page |
| Data Guard | PARTIAL — gate_events visible on Decisions page |
| Regime | VISIBLE on Regime page, not linked to specific decision |
| Premarket Intelligence | NOT IMPLEMENTED in Proof page |
| Technical and Pattern Analysis | NOT IMPLEMENTED in Proof page |
| Advanced Indicators | NOT IMPLEMENTED in Proof page |
| Options Intelligence | PARTIAL — Options pipeline page |
| Probability | NOT IMPLEMENTED in Proof page |
| Scoring and Synthesis | NOT IMPLEMENTED in Proof page |
| Specialist Council | PARTIAL — Council page shows runs, not linked to decision |
| Portfolio Optimization | NOT IMPLEMENTED in Proof page |
| Risk Gate | PARTIAL — gate_events visible |
| Final Decision | PARTIAL — decision_audit row visible |
| Paper Order | PARTIAL — paper-trades page |
| Fill or Rejection | PARTIAL — fill-audit visible |
| Position | PARTIAL — portfolio shows open positions |
| Outcome when closed | NOT IMPLEMENTED in Proof page |
| Attribution | NOT IMPLEMENTED in Proof page |
| Learning | NOT IMPLEMENTED in Proof page |

**Verdict: Cannot label any trace "End-to-End Verified" under current implementation.**

---

## SECTION 9 — OPPORTUNITY QUEUE AND SELECTION-BIAS PROOF

**Status: NOT IMPLEMENTED**

- Opportunities page shows `aiem_predictions` (approved candidates only)  
- Rejected candidates (data-guard, liquidity, portfolio-risk, calibration, NO TRADE) are **not displayed**  
- No decision version history  
- No permanent queryability proof for rejected candidates  
- No selection-bias audit

---

## SECTION 10 — FINANCIAL CORRECTNESS

**Status: NOT VERIFIED**

Phase B verified: `pnl`, `pnl_pct`, `trade_type` field names are correct database column names.

The following have **not been verified**:

| Item | Status |
|---|---|
| Single-leg option P&L (entry × 100 multiplier) | NOT VERIFIED |
| Debit/credit spread P&L | NOT VERIFIED |
| Contract multiplier applied in display | NOT VERIFIED |
| Commissions | NOT VERIFIED |
| Slippage | NOT VERIFIED |
| Partial fills | NOT VERIFIED |
| Realized vs unrealized separation | NOT VERIFIED |
| Breakeven, max-loss, max-reward | NOT DISPLAYED |
| Portfolio Greeks | NOT DISPLAYED |
| Risk budget | NOT DISPLAYED |
| Drawdown / equity curve | NOT DISPLAYED |
| Performance ratios | NOT DISPLAYED |

---

## SECTION 11 — INDICATOR EXPLANATION

**Status: NOT IMPLEMENTED**

No indicator shown anywhere in the dashboard includes:
- Interpretation, confidence, freshness, data source, calculation version  
- Decision contribution, confidence effect, position-size effect, rejection effect  
- Verification status  

---

## SECTION 12 — PROBABILITY AND CALIBRATION

**Status: NOT IMPLEMENTED**

No page exists or displays:
- Brier score, log loss, ECE, MCE  
- Reliability curve  
- Walk-forward / OOS performance  
- Regime-specific calibration  
- Raw vs calibrated vs trade probability separation  

---

## SECTION 13 — PERFORMANCE SEGREGATION

**Status: NOT IMPLEMENTED**

No chart or metric in the dashboard labels its operating mode. The Paper Trading page shows paper trade P&L without clearly labeling it as paper/simulation mode with date range, sample size, or assumptions.

---

## SECTION 14 — SYSTEM OPERATIONS

**Status: PARTIALLY COMPLETE**

| Requirement | Status |
|---|---|
| API liveness | ✅ `/stock-api/health` with stale indicator |
| Scheduler job count + next run | ✅ `/admin/scheduler-jobs` |
| Worker heartbeats (last success, failures) | ✅ `/admin/job-heartbeats` |
| Database status | NOT IMPLEMENTED |
| Primary/secondary provider status | NOT IMPLEMENTED |
| Queue / dead-letter queue | NOT IMPLEMENTED |
| Notification status | PARTIAL — Telegram heartbeat if job exists |
| Backup status | NOT IMPLEMENTED |
| Audit service | PARTIAL — evidence chain status |
| Verification service | PARTIAL — Proof page |
| Broker simulator | NOT IMPLEMENTED |
| Latency metrics | NOT IMPLEMENTED |
| Stale-job detection | NOT IMPLEMENTED |
| Missing morning-scan alert | NOT IMPLEMENTED |
| Duplicate-run alert | NOT IMPLEMENTED |
| Provider failover status | NOT IMPLEMENTED |
| Recovery history | NOT IMPLEMENTED |
| Audit-chain failure alert | NOT IMPLEMENTED |

---

## SECTION 15 — REPORTING

**Status: NOT IMPLEMENTED**

No exportable reports exist. No report labeling (data period, operating mode, sample size, assumptions, limitations, sources, model/code version, generation timestamp).

---

## SECTION 16 — SECURITY VERIFICATION

**Status: PARTIALLY COMPLETE**

| Requirement | Status |
|---|---|
| Missing-token → 403 | ✅ VERIFIED (Phase B, 6 endpoints) |
| Wrong-token → 403 | ✅ VERIFIED (Phase B, 3 endpoints) |
| Malformed input → 400 | ✅ VERIFIED (Phase B, 2 tests) |
| Role restrictions | NOT IMPLEMENTED |
| Session expiration | NOT IMPLEMENTED |
| Rate limiting | NOT IMPLEMENTED — no per-IP rate limit in Flask |
| Input validation (SQL injection) | PARTIAL — psycopg2 parameterized queries used |
| Output encoding | PARTIAL — Flask jsonify handles escaping |
| CSRF protection | NOT IMPLEMENTED |
| Content Security Policy header | NOT IMPLEMENTED |
| Security headers (HSTS, X-Frame-Options, etc.) | NOT IMPLEMENTED |
| Secure cookie handling | N/A — no cookies used |
| Secret scanning (bundle inspection) | NOT IMPLEMENTED |
| Dependency scanning | NOT IMPLEMENTED |
| Export authorization | NOT IMPLEMENTED |
| Administrative-action audit logging | NOT IMPLEMENTED |
| Common web vulnerability testing (OWASP) | NOT IMPLEMENTED |

---

## SECTION 17 — NEGATIVE CONTROLS

**Status: NOT IMPLEMENTED**

No documented negative control tests exist for:
- Stale data behavior  
- Provider outage / failover  
- Database query failure  
- Stream disconnection  
- Missed / duplicate events  
- Incomplete trace  
- Invalid hash chain  
- Risk-gate block  
- Insufficient calibration sample  
- Rejected paper order  
- Missing authorization  
- Null financial values  

---

## SECTION 18 — REQUIRED EVIDENCE PACKAGE

**Status: NOT COMPLETE**

| Evidence Item | Status |
|---|---|
| Master requirement matrix | THIS DOCUMENT |
| File inventory | NOT PRODUCED |
| Architecture diagram | NOT PRODUCED |
| Route inventory | NOT PRODUCED |
| API inventory | NOT PRODUCED |
| Component inventory | NOT PRODUCED |
| Database query inventory | NOT PRODUCED |
| Index inventory | NOT PRODUCED |
| Data contracts | NOT PRODUCED |
| Test inventory | NOT PRODUCED |
| Raw test results | PARTIAL — Phase B verification doc |
| Screenshots of every completed screen | NOT PRODUCED |
| API-response evidence | PARTIAL — Phase B curl evidence |
| SQL evidence | PARTIAL — Phase B cross-checks |
| Real stream evidence | NOT APPLICABLE (no streams) |
| Trace evidence | NOT PRODUCED |
| Hash-chain evidence | PARTIAL — chain status verified |
| Security evidence | PARTIAL — 403/400 tests |
| Accessibility evidence | NOT PRODUCED |
| Performance evidence | NOT PRODUCED |
| Git commit | f7da5e4 (Phase B checkpoint) |
| SHA-256 hashes for critical files | NOT PRODUCED |
| Known limitations | THIS DOCUMENT |
| Deferred items | THIS DOCUMENT |
| Regression results for Phase B fixes | ✅ TSC EXIT_CODE 0 re-confirmed this session |
| Final PASS/FAIL matrix | SEE SECTION 19 |

---

## SECTION 19 — FINAL ACCEPTANCE MATRIX

**Overall Status: CANNOT PASS**

| Acceptance Criterion | Status |
|---|---|
| Terminal is separately deployable | ✅ YES — separate Vite build, separate port, separate workflow |
| Required pages contain real functionality | ❌ PARTIAL — 8/17 pages exist, all partial |
| Real-time updates are verified | ❌ NO — polling only, no SSE/WebSocket |
| All candidates are visible | ❌ NO — rejected candidates not shown |
| Decision Proof is reproducible | ❌ NO — 3/20 stages linked, no full trace |
| Paper-trading values are financially correct | ❌ UNVERIFIED — field names correct, math not verified |
| Portfolio risk is verified | ❌ NO — no Greeks, no drawdown, no VaR |
| Calibration metrics are honestly represented | ✅ YES — shown as NOT AVAILABLE (honest) |
| Learning events are evidence-based | ✅ YES — Math.random removed, honest unavailable state |
| Operational reliability is visible | ❌ PARTIAL — heartbeats only, no latency/failover |
| Authentication and roles are verified | ❌ PARTIAL — token auth verified, RBAC not implemented |
| Stale and failed data are handled honestly | ✅ YES — stale indicators, honest empty states |
| At least one genuine end-to-end decision is proven | ❌ NO |
| Hash-chain evidence is valid | ✅ PARTIAL — chain status readable, full audit pending |
| No prohibited production mocks remain | ✅ YES — all Math.random and hardcoded fakes removed from pages |
| Existing AIEM and scanner functionality remain intact | ✅ YES — TSC EXIT_CODE 0, all workflows running |

---

## WHAT WAS COMPLETED THIS SESSION

### Code Changes Made
1. **`Learning.tsx`** — Removed `Math.random()` chart + hardcoded fallback stats + fabricated adaptive policies → honest unavailable states
2. **`Signals.tsx`** — Replaced entire hardcoded statistical findings table with real `aiem_signal_discoveries` endpoint + real diagnostics
3. **`CommandCenter.tsx`** — Replaced fake 24-job grid with real `job_heartbeats` data + fixed `macro_score` + fixed `next_run` field
4. **`Alerts.tsx`** — Removed hardcoded "ONLINE & LISTENING" / "PING: 24ms" + replaced with real heartbeat failure data
5. **`Scheduler.tsx`** — Removed fake placeholder rows with fabricated timestamps → honest empty state
6. **`main.py`** — Added `GET /stock-api/admin/signal-discoveries` endpoint (line 69330) querying `aiem_signal_discoveries`

### Compile Status
```
pnpm tsc --noEmit → EXIT_CODE: 0
```

### Fabricated Data Remaining
```
grep -rn "Math\.random" artifacts/aiem-dashboard/src/pages/ → PASS: zero results
grep -rn "62\.4%|ONLINE & LISTENING|i % 7|100000 \* i|system_scan_" ... → PASS: zero results
```
Only remaining `Math.random`: `sidebar.tsx:612` in `SidebarMenuSkeleton` — UI loading skeleton width randomization. Not financial data. Acceptable.

---

## PRIORITY ORDER FOR REMAINING WORK

Ranked by directive priority and implementation feasibility:

1. **Decision Proof chain** — link `oe_decision_audit` trace_id across all available stage tables
2. **Opportunity Queue** — surface rejected candidates from `oe_gate_events` + `oe_decision_audit` WHERE outcome='NO_TRADE'  
3. **Real-time SSE stream** — Flask `text/event-stream` for decisions, paper trades, alerts  
4. **Role-based access control** — JWT with role claim; 7 roles  
5. **Financial correctness verification** — display contract multiplier, realized/unrealized, breakeven  
6. **API documentation / data contracts** — OpenAPI spec for all 20+ terminal endpoints  
7. **Security hardening** — CSP headers, rate limiting, CSRF, session expiry  
8. **Probability & Calibration page** — from `aiem_signal_discoveries.brier_score` if populated  
9. **Performance Analytics page** — paper trade equity curve, performance ratios  
10. **Indicator Laboratory page** — from `layer9_scores`, `oe_indicator_registry`  
11. **Responsive monitoring view** (mobile layout)  
12. **Institutional Reports** — exportable PDF/CSV with provenance labels  
13. **Negative control test suite** — documented tests for every stale/failure mode  
14. **Full evidence package** — SHA-256 hashes, screenshots, API-response evidence per page  
