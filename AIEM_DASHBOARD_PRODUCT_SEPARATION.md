# AIEM DASHBOARD — PHASE A
## Product Separation Confirmation (Section 4 Response)
**Generated:** 2026-07-21

---

## Current Architecture Reality

**Critical finding:** The three products are NOT currently cleanly separated. All three share:
- A single Flask application (`artifacts/stock-scanner-api/main.py`, 69,000 lines)
- A single PostgreSQL database
- A single deployment unit

The architecture described below is the **target** for independent deployability. The current state requires migration work before any product can be sold independently.

---

## Product 1 — Stock Scanner (Customer-Facing)

**Current separation:** PARTIAL  
**Current location:** `artifacts/stock-scanner/` (React frontend) + sections of main.py  
**Routes owned:** ~120 of 333 (public scanner tabs — unusual-calls, net-flow, conviction-calls, etc.)  
**DB tables owned:** ~60 tables (conviction_calls_watchlist, unusual_calls_log, call_sweep_log, etc.)  
**Separately deployable today:** NO — Flask app is shared with AIEM

### Target Architecture (for independent sale)
| Component | Owner | Interface | Auth | Data Contract |
|-----------|-------|-----------|------|--------------|
| Scanner API | Stock Scanner | REST HTTP | None / Stripe subscription check | polygon_market_daily (read-only) |
| Scanner DB | Stock Scanner | PostgreSQL (separate schema or instance) | DB credentials | Own tables only |
| Scanner Frontend | Stock Scanner | Vite/React SPA | Stripe paywall | All JSON via scanner API |

**Failure behavior if AIEM removed:** Scanner continues serving all public tabs. No dependency on AIEM decision logic.  
**Replacement method:** Fork scanner routes into a new Flask app. Estimated effort: 2-3 weeks.

---

## Product 2 — AIEM (Institutional Decision Engine)

**Current separation:** PARTIAL  
**Current location:** `artifacts/stock-scanner-api/` (shared Flask) + `aiem_*.py` modules  
**Routes owned:** ~80 of 333 (aiem-paper-portfolio, aiem/chat, probability-engine, etc.)  
**DB tables owned:** `aiem_*` (110 tables), `d3_*` (25 tables), `oe_*` (45 tables via options intelligence sub-system)  
**Separately deployable today:** NO — Flask app is shared with Stock Scanner

### Target Architecture
| Component | Owner | Interface | Auth | Data Contract |
|-----------|-------|-----------|------|--------------|
| AIEM API | AIEM | REST HTTP + SSE | X-Admin-Token (per-tenant) | aiem_* + oe_* + d3_* tables |
| AIEM Scheduler | AIEM | Internal APScheduler | No external interface | Writes to aiem_paper_trades, options_pipeline_jobs |
| AIEM DB | AIEM | PostgreSQL (separate instance) | DB credentials | aiem_* + oe_* + d3_* schema |
| AIEM Dashboard | AIEM | React SPA | ADMIN token → sessionStorage | All JSON via AIEM API |

**Shared infrastructure with Stock Scanner (replaceable interfaces):**
- `polygon_market_daily` — both products read; AIEM-only deployment would need own Polygon feed
- `ticker_market_cap_cache` — both products read; AIEM copy separated trivially
- `telegram_alert_ledger` — AIEM-owned; scanner has no direct dependency
- ADMIN_TOKEN auth — AIEM only; scanner uses Stripe

**Failure behavior if Stock Scanner removed:** AIEM continues fully. All AIEM pipelines are self-contained in aiem_* modules. The shared Flask server becomes AIEM-only.

**Failure behavior if AIEM removed:** Stock Scanner continues serving all public tabs. AIEM paper trades tab disappears from scanner site (was gated via signal_isolation_rule anyway).

**Replacement method:** Extract aiem_* routes and modules into a new Flask app. Estimated effort: 3-4 weeks.

---

## Product 3 — Options Engine

**Clarification:** The Options Engine (`oe_*` tables, `aiem_options_scheduler.py`) is **NOT a separate product** — it is AIEM's options intelligence sub-system. It is owned entirely by AIEM.

**Evidence:**
- `aiem_options_scheduler.py` is the scheduler — named `aiem_*` not a standalone product
- All oe_* tables are written only by `aiem_options_pipeline.py` and `aiem_options_scheduler.py`
- The D3 governance system (`d3_*` tables) links options decisions to paper trades
- There is no separate "Options Engine" UI, API, or deployment

**Correct framing for independent sale:**
- If "Options Engine" means AIEM's options intelligence capability — it can be sold as a feature of AIEM, not a standalone product
- If a standalone Options Engine product is desired, it would require extracting `aiem_options_scheduler.py` + `aiem_options_pipeline.py` + all `oe_*` tables into their own Flask app + DB + frontend

**Estimated effort to extract as standalone product:** 4-6 weeks (significant schema refactoring required; D3 governance is tightly coupled to the paper trading system)

---

## Shared Infrastructure Audit

| Component | Current Owner | Used By | Replacement if Separated |
|-----------|--------------|---------|--------------------------|
| `polygon_market_daily` (3.3M rows) | Stock Scanner (primary filler) | Both | AIEM runs its own Polygon grouped-daily fetch |
| `td_intraday_cache` (145K rows) | AIEM aiem-process | Stock Scanner tabs read it | Stock Scanner uses its own Tradier fetch |
| `ticker_market_cap_cache` | aiem-process | Both | Trivial to duplicate |
| `main.py` Flask app | Shared | Both | Split into 2 Flask apps |
| `DATABASE_URL` | Shared | All products | Separate DB instance per product |
| `ADMIN_TOKEN` | AIEM | AIEM admin routes only | AIEM keeps it; scanner drops it |
| Telegram bot | AIEM | AIEM alerts only | Scanner builds own if needed |

---

## Cross-Product Contamination Risks

| Risk | Location | Severity | Remediation |
|------|----------|----------|-------------|
| AIEM signal leaking to scanner tabs | aiem-signal-isolation-rule.md (documented) | MEDIUM | Rule enforced in code — no injection into scanner tabs |
| Shared DB means scanner can query AIEM tables | DB-level — no row security | HIGH | Add PostgreSQL RLS or separate DB instances |
| `is_test_record` rows could bleed into both products | D3 tables | LOW | No test rows currently; enforce via query filter |
| `audit_trace_id` appears in `telegram_alert_ledger` | telegram_alert_ledger | LOW | Telegram is AIEM-owned, alerts go to owner only |

---

## Verdict on Independent Deployability

| Question | Stock Scanner | AIEM | Options Engine |
|----------|--------------|------|---------------|
| Separate codebase today? | NO | NO | N/A (part of AIEM) |
| Separate DB today? | NO (shared) | NO (shared) | NO |
| Separate deployment today? | NO | NO | N/A |
| Independently deployable with ~3 weeks work? | YES | YES | Only as AIEM sub-feature |
| Can continue operating if other product sold? | YES (no AIEM dependency) | YES (no scanner dependency) | Stays with AIEM |
