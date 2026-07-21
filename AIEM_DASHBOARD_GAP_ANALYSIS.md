# AIEM DASHBOARD — PHASE A
## Gap Analysis
**Generated:** 2026-07-21 | **Classification:** P0 (blocker) → P3 (nice-to-have)

---

## Summary
- **P0 gaps (blocking dashboard launch):** 3
- **P1 gaps (significantly limits value):** 7
- **P2 gaps (limits specific screens):** 9
- **P3 gaps (nice-to-have):** 5

---

## P0 — Blocking Dashboard Launch

### G-P0-1: No dedicated dashboard frontend application
**What's missing:** No React/Vite dashboard app exists for the AIEM engine. The existing `artifacts/stock-scanner/` frontend is a public-facing customer product, not the AIEM operator dashboard.  
**Impact:** There is no screen to put data on. Every other item in this report is moot without this.  
**Resolution:** Create `artifacts/aiem-dashboard/` as a new React+Vite artifact, protected by ADMIN_TOKEN header injection.  
**Effort:** Large (Phase B task)  
**Blocked by:** Nothing — can start immediately.

### G-P0-2: No real-time push for live trade events
**What's missing:** The frontend has no EventSource implementation. The SSE endpoint (`/stock-api/aiem/chat/stream`) is chat-only. There is no event stream for: new paper trade opened, gate fired, governance decision, pipeline job state change.  
**Impact:** Dashboard operator cannot see live pipeline execution without manually refreshing.  
**Resolution (interim):** Polling every 10-30s against existing endpoints is sufficient for Phase B.  
**Resolution (full):** Extend SSE endpoint to emit pipeline events via `aiem_communication_bus.py`.  
**Effort:** Medium (polling acceptable for Phase B)  
**Blocked by:** G-P0-1

### G-P0-3: ADMIN_TOKEN auth not wired to any frontend UX
**What's missing:** 58 admin routes require `ADMIN_TOKEN` header. No frontend auth flow exists (login, token storage, or header injection).  
**Impact:** Admin-only screens cannot be accessed without raw curl or Postman.  
**Resolution:** Dashboard frontend must include a one-time ADMIN_TOKEN entry → sessionStorage → injected into all admin API calls.  
**Effort:** Small  
**Blocked by:** G-P0-1

---

## P1 — Significantly Limits Value

### G-P1-1: oe_decision_audit has no API route
**Table:** `oe_decision_audit` (341 rows, written 2026-07-19 to 2026-07-21)  
**What's missing:** No GET endpoint exposing this table. Contains the strongest audit record (cryptographic hash chain per decision, JSONB payloads for all 6 decision layers).  
**Resolution:** Add `GET /stock-api/admin/decision-audit?date=&ticker=&limit=` — DB query with is_test_record=FALSE filter.  
**Effort:** Small (add 1 route)  
**Note:** This is the most important missing route.

### G-P1-2: oe_gate_events has no API route
**Table:** `oe_gate_events` (4 rows, written 2026-07-21)  
**What's missing:** No GET endpoint. Gate events are the primary evidence that governance enforcement fired.  
**Resolution:** Add `GET /stock-api/admin/gate-events?date=&ticker=&limit=`  
**Effort:** Small (add 1 route)

### G-P1-3: aiem_specialist_council_runs has no API route
**Table:** `aiem_specialist_council_runs` (219 rows, written 2026-07-12 to 2026-07-21)  
**What's missing:** No GET endpoint. Council debates are central to explaining AIEM decisions.  
**Resolution:** Add `GET /stock-api/admin/council-runs?trace_id=&ticker=&limit=`  
**Effort:** Small (add 1 route)

### G-P1-4: paper_trade_job_ledger has no API route
**Table:** `paper_trade_job_ledger` (5 rows, written 2026-07-15 to 2026-07-21)  
**What's missing:** No GET endpoint exposing run history: which trigger fired, how many picks, completed_at.  
**Resolution:** Add `GET /stock-api/admin/paper-job-ledger`  
**Effort:** Small (add 1 route)

### G-P1-5: aiem_position_sizing_log has no API route
**Table:** `aiem_position_sizing_log` (207 rows, written 2026-07-12 to 2026-07-21)  
**What's missing:** No GET endpoint. Position sizing decisions (conviction_score, entry_price, notional) are critical for risk review.  
**Resolution:** Add `GET /stock-api/admin/position-sizing-log?ticker=&limit=`  
**Effort:** Small (add 1 route)

### G-P1-6: No unified trace explorer
**What's missing:** Given a `ticker + date`, an operator cannot get a single page showing the complete trace across all 4 chains (paper trade → council → supervisor audit → governance).  
**Resolution:** Add `GET /stock-api/admin/trace-explorer?ticker=&date=` — composite query joining all 4 chains.  
**Effort:** Medium (complex JOIN, but all data exists)

### G-P1-7: evidence_chain.log not queryable
**What's missing:** The SHA-256 hash chain (SEQ counter) lives in `/home/runner/workspace/artifacts/stock-scanner-api/dpl/evidence_chain.log` — a file, not a DB table. Dashboard cannot display current SEQ or verify chain integrity without file access.  
**Resolution:** Add `GET /stock-api/admin/evidence-chain/status` — reads last N lines of evidence_chain.log and returns JSON.  
**Effort:** Small

---

## P2 — Limits Specific Screens

### G-P2-1: daily_pipeline_runs has no API route
**Table:** `daily_pipeline_runs` (6 rows) — written by options-pipeline-scheduler external failover system.  
**Resolution:** Add `GET /stock-api/admin/daily-pipeline-runs`  
**Effort:** Tiny

### G-P2-2: Portfolio state is in-memory only
**Endpoint:** `/stock-api/portfolio` serves from `app._portfolio` (in-memory dict). Resets on every restart.  
**Resolution:** Persist portfolio state to a DB table; `/stock-api/portfolio` reads from DB with in-memory fallback.  
**Effort:** Medium

### G-P2-3: No pagination on core endpoints
**Affected routes:** `/stock-api/aiem-paper-portfolio`, `/stock-api/aiem-pipeline-audit`, `/stock-api/admin/supervisor-daily-report`  
**Resolution:** Add `?limit=&offset=` or `?date=` query params.  
**Effort:** Small per route

### G-P2-4: oe_indicator_snapshots has no API route
**Table:** `oe_indicator_snapshots` (1,739 rows) — contains all 79 registered indicator readings per pipeline run.  
**Resolution:** Add `GET /stock-api/admin/indicator-snapshots?trace_id=&date=`  
**Effort:** Small

### G-P2-5: d3_governance_decisions has no dedicated list route
**Table:** `d3_governance_decisions` (94 rows) — queryable only through `/stock-api/admin/module4-history` (limited).  
**Resolution:** Add `GET /stock-api/admin/governance-decisions?checkpoint=&date=&limit=`  
**Effort:** Small

### G-P2-6: No SSE reconnect / Last-Event-ID support
**Affected:** `/stock-api/aiem/chat/stream`  
**Impact:** Browser disconnects lose events; no catch-up on reconnect.  
**Resolution:** Add `Last-Event-ID` header handling to SSE endpoint.  
**Effort:** Small

### G-P2-7: aiem_pipeline_audit_log missing some stages
**Observed:** `aiem_pipeline_audit_log` has 284 rows but logged_at range is 2026-07-11 to 2026-07-21, suggesting sparse logging on some days. Some pipeline runs may not have full stage coverage.  
**Resolution:** Audit which stages use `log_pipeline_event()` vs fire silently.  
**Effort:** Investigation only

### G-P2-8: No webhook / push for Telegram alerts to dashboard
**Table:** `telegram_alert_ledger` (1,144 rows) — written but only queryable via direct DB read, not via API.  
**Resolution:** Add `GET /stock-api/admin/telegram-alerts?date=&limit=`  
**Effort:** Tiny

### G-P2-9: No route for aiem_supervisor_risk_review
**Table:** `aiem_supervisor_risk_review` (3 rows) — supervisor risk assessments, no API exposure.  
**Resolution:** Add to supervisor-summary endpoint or new route.  
**Effort:** Tiny

---

## P3 — Nice-to-Have

### G-P3-1: No dark mode support
No CSS framework dark mode detected in stock-scanner frontend. Dashboard should default to dark.

### G-P3-2: No alert count badge on dashboard nav
`/stock-api/alerts/count` (line 48886) exists and returns count. Dashboard nav should poll this.

### G-P3-3: No mobile layout for operator dashboard
Dashboard pages with dense tables will need responsive wrappers for phone access.

### G-P3-4: No CSV export for audit tables
oe_decision_audit, aiem_pipeline_audit_log useful as CSV exports for offline analysis.

### G-P3-5: No CHANGELOG feed on dashboard
D3 change log (32 rows in d3_change_log) useful as a dashboard feed but no API route.

---

## Gap Impact Matrix

| Screen | P0 Gaps | P1 Gaps | P2 Gaps | Can Launch With Polling? |
|--------|---------|---------|---------|--------------------------|
| Command Center | G-P0-1,G-P0-3 | — | — | YES after P0 resolved |
| Live Decisions | G-P0-1,G-P0-3 | G-P1-1,G-P1-2 | G-P2-4 | PARTIAL (pipeline jobs yes, decision audit no) |
| Opportunity Queue | G-P0-1 | — | — | YES after P0 resolved |
| Decision Proof | G-P0-1,G-P0-2 | G-P1-1,G-P1-7 | — | PARTIAL |
| Paper Trading | G-P0-1,G-P0-3 | G-P1-4 | G-P2-3 | YES after P0 resolved |
| Portfolio Risk | G-P0-1 | — | G-P2-2 | PARTIAL |
| Specialist Council | G-P0-1,G-P0-3 | G-P1-3 | — | PARTIAL |
| Indicator Laboratory | G-P0-1 | — | G-P2-4 | YES after P0 resolved |
| Probability & Calibration | G-P0-1 | — | — | YES after P0 resolved |
| Performance Analytics | G-P0-1 | — | G-P2-3 | YES after P0 resolved |
| Learning Center | G-P0-1,G-P0-3 | — | — | YES after P0 resolved |
| Research & Hypotheses | G-P0-1 | — | — | YES after P0 resolved |
| Audit & Verification | G-P0-1,G-P0-3 | G-P1-1,G-P1-2,G-P1-6,G-P1-7 | G-P2-5 | PARTIAL |
| AIEM Chat | G-P0-1,G-P0-3 | — | G-P2-6 | YES after P0 resolved |
| System Operations | G-P0-1,G-P0-3 | G-P1-4 | G-P2-1,G-P2-8 | YES after P0 resolved |
| Administration | G-P0-1,G-P0-3 | — | — | YES after P0 resolved |

---

## Recommended Phase B Build Order

1. **Create dashboard artifact** (resolves G-P0-1)
2. **Add ADMIN_TOKEN UX** (resolves G-P0-3)  
3. **Add 6 missing routes** (G-P1-1 through G-P1-4 + G-P1-7 + G-P2-8) — all tiny/small effort
4. **Build polling-based live updates** (resolves G-P0-2 interim)
5. **Trace explorer endpoint** (G-P1-6)
6. **Pagination on core endpoints** (G-P2-3)
