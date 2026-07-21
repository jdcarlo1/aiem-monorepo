# 
 AIEM Institutional Terminal — Master Status & Document Registry
**Generated:** 2026-07-21  
**Status:** Document ingestion complete. Awaiting 760-item checklist source to begin full verification.

---

## GOVERNING DOCUMENT FRAMEWORK

Five binding documents. None supersedes another. All must be fully satisfied for FINAL PASS.

| # | Document | Sections | Role | Received |
|---|---|---|---|---|
| 1 | **30-Section Master Design Directive** | 30 | Product, architecture, UX, and functionality authority | ✅ |
| 2 | **AIEM_FINAL_COMPLETION_MASTER_CHECKLIST.md** | 760 items | Detailed implementation and verification authority | ⏳ Pending (7 files) |
| 3 | **28-Section Remediation Directive** | 28 | Institutional security, audit, financial-correctness, reliability overlay | ✅ |
| 4 | **Design Alignment Remediation Directive** | 15 | Naming, gaps, and completeness overlay | ✅ |
| 5 | **760-Item Condensed Audit Remediation Directive** | 23 | Supplemental control overlay on #2 | ✅ |

**FINAL PASS requires all five documents satisfied. Dashboard construction does not begin until the 760-item checklist is fully verified.**

---

## DOCUMENT 1 — 30-SECTION MASTER DESIGN DIRECTIVE

### Summary
Build a completely separate, production-quality institutional terminal exclusively for the AIEM intelligence and autonomous paper-trading system. Must feel like a professional institutional trading terminal, not a retail dashboard.

### 16 Navigation Sections Required
1. Command Center (default homepage)
2. Live Decisions (Decisions)
3. Opportunity Queue
4. Decision Proof
5. Paper Trading Center
6. Portfolio Risk Center
7. Specialist Council
8. Indicator Laboratory
9. Probability & Calibration
10. Performance Analytics
11. Learning Center (Learning Loop)
12. Research & Hypotheses (Signal Discoveries)
13. Audit & Verification
14. System Operations
15. Reports & Due Diligence
16. Administration

### Design System: AIEM OBSIDIAN TERMINAL
- Deep charcoal / near-black backgrounds
- Amber: active focus and section identity
- Green: approved, profitable, healthy, verified
- Red: rejected, failing, blocked, critical
- Cyan: live market values and informational data
- Purple: model, learning, and probability analytics
- White: primary text only
- Monospaced numerals for all financial and technical data
- No Bloomberg branding, no retail cards, no excessive gradients, no AI robot imagery

### Global Terminal Shell Required
- Top system bar (logo, time ET, session status, regime, health indicators, user, alerts)
- Left navigation (16 sections)
- Global search / command palette with keyboard commands (TICKER, TRACE, TRADE, DECISION, HEALTH, RISK, etc.)
- Bottom event ticker (continuously updating, pausable, filterable)

### Technology Stack
- React + TypeScript + Vite
- TanStack Query for server state
- Zustand for UI state
- WebSocket / SSE for live updates
- Institutional-quality charting library
- Virtualized tables
- Component-level error boundaries
- Dark-mode-first

### Real-Time Streams Required
- `/stream/decisions`
- `/stream/trades`
- `/stream/orders`
- `/stream/risk`
- `/stream/system`
- `/stream/audit`
- `/stream/alerts`

### API Endpoint Groups Required
```
/api/v1/terminal/summary
/api/v1/decisions
/api/v1/decisions/{decision_id}
/api/v1/decisions/{decision_id}/proof
/api/v1/candidates
/api/v1/paper-trades
/api/v1/paper-trades/open
/api/v1/paper-trades/closed
/api/v1/orders
/api/v1/portfolio
/api/v1/portfolio/risk
/api/v1/portfolio/greeks
/api/v1/portfolio/scenarios
/api/v1/indicators
/api/v1/indicators/{ticker}
/api/v1/council
/api/v1/calibration
/api/v1/performance
/api/v1/learning
/api/v1/hypotheses
/api/v1/audit
/api/v1/traces/{trace_id}
/api/v1/system/health
/api/v1/system/incidents
/api/v1/reports
```

### Decision Proof Pipeline (all stages required)
```
Market Data → Data Quality Guards → Macro/Regime → Premarket Intelligence
→ Technical/Pattern Analysis → Advanced Quant Indicators → Options Intelligence
→ Probability Engine → Scoring/Synthesis → Specialist Council
→ Portfolio Optimization → Risk Gate → Final Decision
→ Paper Execution → Outcome → Attribution → Learning → Memory
```
Each stage must display: status, timestamps, duration, input/output IDs, summary, data source, model version, errors/warnings, trace ID, audit event ID, hash-chain status.

### Release 1 Must Include (20 items)
Authentication and roles, global terminal shell, all 16 navigation sections, real-time streams, responsive monitoring, saved user workspaces, exportable institutional reports, complete automated test coverage.

### Strict Rules
- No fabricated dashboard values
- No mock proof as production evidence
- No manual database inserts
- No frontend bypassing portfolio or risk gates
- No live-money trading (paper only)
- No secrets exposed in browser
- AIEM remains the decision authority

---

## DOCUMENT 3 — 28-SECTION REMEDIATION DIRECTIVE

### Key Requirements

**Acceptance Status:** PASS / PARTIAL / FAIL / NOT IMPLEMENTED / BLOCKED  
Every status requires a technical explanation. BLOCKED may not hide missing implementation.

**Acceptance Classification:** CRITICAL / MANDATORY / CONDITIONAL / OPTIONAL  
CRITICAL includes: fabricated data removal, auth/authorization, financial calculations, Risk Gate, portfolio limits, strategy disable, audit chain, decision trace, scheduler-to-outcome proof, outcome-to-learning proof, DB/API reconciliation, secret protection, SQL injection, XSS, CSRF, production/paper-trading labeling, provider failure handling, scheduler recovery, no duplicate orders, no unauthorized operational controls.

**FINAL PASS prohibited if any CRITICAL item is FAIL, PARTIAL, NOT IMPLEMENTED, or BLOCKED.**

**Evidence Fields Required (per item):** Requirement ID, text, acceptance class, status, technical conclusion, files changed, functions/classes changed, API endpoint, DB table, SQL query, raw SQL result, test command, raw test result, browser evidence, runtime evidence, security evidence, negative-control evidence, trace_id, candidate_id, decision_id, execution_plan_id, order_id, position_id, outcome_id, learning_event_id, audit_log_id, verification_run_id, evidence hash, SHA-256 file hash, git commit, deployment ID, environment ID, regression result, limitations, reviewer conclusion.

**Verification Run Identity Required:**
- verification_run_id, environment name/ID, hostname, git commit SHA, git branch, git status --porcelain, deployment ID, container-image digest, frontend bundle hash, backend dependency-lock hash, database migration revision, Python version, Node version, OS version, test start/end timestamps, timezone, model version, calculation version.

**Architecture Additions:**
- Dashboard CPU/memory cannot starve AIEM workers
- Isolated processes/services
- Dashboard DB pools cannot exhaust AIEM connections
- Circuit breakers for failing dashboard dependencies
- Failed deployments can be rolled back
- Health checks distinguish liveness from readiness
- Production startup fails safely on missing config
- RPO and RTO documented

**Fabricated-Data Removal:** AST scan, bundle scan, backend fallback scan, API middleware scan, runtime network inspection, API-to-SQL reconciliation, empty-DB test, null-value test, provider-outage test. Synthetic data labeled SYNTHETIC/TEST/BACKTEST/SIMULATION with generation method, ID, timestamp, assumptions.

**API Hardening:** Idempotency, duplicate prevention, correlation IDs, max request/response sizes, rate limits, race-condition tests, optimistic locking, decimal precision (no unsafe binary float for exact decimal), DST testing, errors never expose secrets/stack traces.

**Auth/RBAC:** MFA for privileged roles, default-deny, formal role-permission matrix, separation of duties, break-glass procedure, token issuer/audience validation, signing-key rotation, session concurrency policy, active session listing and revocation, row-level authorization, periodic access review.

**Decision-Proof Trace Matrix (22 traces required):**
1. Approved stock paper trade
2. Approved single-leg option trade
3. Approved multi-leg option trade
4. NO_TRADE decision
5. Data-Guard rejection
6. Stale-data rejection
7. Liquidity rejection
8. Calibration rejection
9. Risk-Gate rejection
10. Portfolio-risk rejection
11. Execution-cost rejection
12. Insufficient-confidence rejection
13. Paper-order rejection
14. Partial fill
15. Closed outcome linked to attribution
16. Closed outcome linked to learning
17. Primary-provider failure
18. Provider failover
19. Failed scheduler run
20. Scheduler recovery
21. Duplicate candidate suppression
22. Duplicate order suppression

**Audit-Chain Trust Hardening:** Hash chain alone insufficient — must implement at least one: append-only/WORM storage, signed root hash, off-host hash replication, external timestamped anchor, or independently controlled evidence repository. Six negative controls must detect alterations: event content, prev_hash, chain_hash, sequence, timestamp, source record ID.

**Financial Correctness (20+ option structures):** Long/short call, long/short put, long/short stock, debit spreads, credit spreads, calendars, diagonals, butterflies, iron butterflies, condors, iron condors, ratio spreads, backspreads, straddles, strangles, collars, protective positions, synthetic positions, box spreads. Plus: commissions, fees, slippage, bid/ask cost, partial fills, exercise, assignment, expiration, corporate actions, dividend effects, pin risk.

**Portfolio Risk Additions:** Concurrent-order atomicity test (two simultaneous orders independently valid but jointly violating a limit must be atomically blocked). VaR/CVaR methodology, kill switch, quarantine, daily-loss reset timezone.

**Regression Requirements (22 areas):** Stock scanner, AIEM scheduler, morning scan, data ingestion, data guards, probability engine, risk gate, decision engine, paper trading, options strategy registry, portfolio risk, alerts, audit chain, learning loop, Telegram, dashboard frontend, API, database migrations, authentication, role authorization, reports, exports, saved workspaces, real-time event delivery.

---

## DOCUMENT 4 — DESIGN ALIGNMENT REMEDIATION DIRECTIVE

### Key Requirements

**Product Naming:** Choose ONE name — AEIM or AIEM — and use it consistently in: application title, repository, API, database metadata, reports, documentation, routes, components, UI, evidence package.

**Page Name Standardization:**
- Live Decisions = Decisions (if renamed, document equivalence)
- Learning Center = Learning Loop (if renamed, document equivalence)
- Research & Hypotheses = Signal Discoveries (if renamed, document equivalence)
- Audit & Verification: must exist as explicit page
- Administration: must exist as explicit page

**Memory Stage Requirements:**
- `memory_id`, timestamp, status, version, update result, rejected memory events, memory audit trail
- Decision Proof must show whether memory: Created / Updated / Skipped / Rejected

**Global Terminal Shell:** Top bar, command palette, keyboard commands, recent/saved searches, bottom event ticker, event filtering, pause, replay — all must have automated tests.

**Design System Verification:** Dark institutional appearance, amber active states, cyan live data, purple learning analytics, green success, red failures, white primary text, monospaced financial numerals, no Bloomberg branding, no retail cards, accessibility testing confirming status never conveyed by color alone.

**Draggable Workspace:** Panel dragging, resizing, layout persistence, workspace restore, multi-monitor and ultrawide layouts.

**Indicator Inventory:** Every indicator classified as exactly one of: Implemented / Verified / Unavailable / Deferred / Disabled / Retired / Unknown. None may silently disappear.

**Scenario Tests:** All labeled `MODELED ESTIMATE`.

**Trade Journal:** Every paper trade has an immutable journal: recommendation, evidence, order history, adjustments, alerts, risk events, exit decision, outcome, learning, memory, audit chain.

---

## DOCUMENT 5 — 760-ITEM CONDENSED AUDIT REMEDIATION DIRECTIVE

### Key Requirements (23 sections)

**Section 1 — Status and Severity:** PASS/PARTIAL/FAIL/NOT IMPLEMENTED/BLOCKED + CRITICAL/HIGH/MEDIUM/LOW per item. CRITICAL items: auth, authorization, Risk Gate, financial calculations, orders, portfolio controls, scheduler, morning scan, DB integrity, audit chain, provider failover, backups, secrets, evidence integrity.

**Section 2 — Verification Identity:** Every run records verification_run_id, test_run_id, environment, timestamps, git branch/commit, deployment ID/timestamp, frontend/backend build hashes, DB schema version, config version, model version, evidence-generator version. Must prove tested deployment matches recorded commit and build.

**Section 3 — Database Concurrency/Idempotency:** Prevent: duplicate candidates/decisions/orders/fills/scheduler runs, double buying-power reservation, two workers claiming one item, negative quantities, stale portfolio-limit approval, partial records after rollback. Verify: transactions/isolation levels, locking, deadlock handling, rollback, idempotency keys, concurrent identical requests, timeout+retry, network interruption during mutation.

**Section 4 — Auth Matrix:** Full authorization matrix for every page, route, endpoint, export, operational action, paper-trading action, risk action, administrative action. Every allowed and denied role combination tested. Frontend button hiding is never authorization.

**Section 5 — Backup/DR:** Define RPO, RTO, backup frequency, encryption, retention, off-site storage, integrity checking. At least one genuine restoration must pass. Backup-status display alone is insufficient.

**Section 6 — Time/Clock Integrity:** Canonical backend timezone, ET market-session logic, DST transitions, market holidays, early closes, clock-drift measurement, future/duplicate/out-of-order timestamp handling.

**Section 7 — Data Lineage:** Every critical value traceable: Provider → Raw ingestion → Normalization → Calculation → Stored result → API field → Dashboard field → Report. Values: prices/volume, options chains/Greeks, probabilities/confidence, EV, execution costs, portfolio risk, P&L, VaR/CVaR, calibration, performance.

**Section 8 — Decision Lifecycle/Memory:** Full lifecycle verified including Memory stage. Memory fields: `memory_event_id`, source `learning_event_id`, memory version, status, timestamp, content hash, write result, rejection reason, rollback result, audit-log ID. Statuses: CREATED/UPDATED/SKIPPED/REJECTED/ROLLED_BACK. Only verified outcomes may update memory.

**Section 9 — Model/Calibration Governance:** Per model: owner, purpose, training/validation/OOS periods, feature inventory, training-code hash, model-artifact hash, approval/deployment status, champion/challenger status, rollback version, calibration method, minimum sample requirements, leakage/look-ahead/survivorship-bias testing, feature and concept drift, retirement process.

**Section 10 — Financial Correctness Edge Cases:** Stock splits, dividends, adjusted contracts, early exercise/assignment, expiration, pin risk, cash/physical settlement, multiple partial fills, cancellation after partial fill, legging risk, commission allocation, decimal precision, zero/invalid prices, missing/stale/crossed quotes, unbounded-risk detection.

**Section 11 — Portfolio Snapshot Consistency:** Every decision references versioned portfolio snapshot with: cash, buying power, reserved BP, positions, pending orders, P&L, Greeks, concentration, correlation, risk budget, applicable limits. Verify: atomic buying-power use, server-side limit enforcement, snapshot reconciliation.

**Section 12 — Stress Testing:** ±1%, ±2%, ±5% underlying; IV expansion/contraction; skew/term-structure shocks; sector shock; correlation spike; liquidity deterioration; overnight gap; provider outage; combined shocks. All labeled `MODELED ESTIMATE`.

**Section 13 — Execution Quality:** Fill probability, slippage, spread cost, market impact, exit liquidity, commission schedule, quote age, expected vs actual fill/cost, rejection reasons. Reconcile: Expected Net Edge = Expected Trade Edge − Slippage − Commission − Spread Cost − Market Impact.

**Section 14 — Audit-Chain Integrity:** Canonical serialization, hash algorithm version, genesis record, previous-hash validation, missing/duplicate records, unauthorized modification detection, independent recomputation, chain continuity across deployments and restoration. Dashboard cannot edit audit records. Normal admin cannot delete audit records.

**Section 15 — Report/Export Integrity:** Every report includes: report_id, schema version, generation-code hash, data-query hash, DB snapshot reference, content hash, requesting user/role, timestamp, operating mode. PDF/CSV reconciles with API, SQL, dashboard.

**Section 16 — Capacity/SLOs:** Define limits for concurrent users, real-time connections, events/sec, API requests/sec, DB connections. Run load tests. Record p50/p95/p99 latency, throughput, error rate.

**Section 17 — Incident Runbooks:** Tested runbooks for 15 scenarios: DB outage, provider outage, scheduler failure, missing morning scan, duplicate run, event-stream outage, risk-service outage, audit-service outage, evidence-chain failure, auth compromise, secret exposure, deployment failure, backup failure, corrupt data, incorrect financial calculation.

**Section 18 — Design/Nav/UX Alignment:** Select one AIEM/AEIM spelling, global system bar, command palette, bottom event ticker, event filtering/pause/replay, explicit Audit & Verification and Administration pages, institutional dark terminal, consistent typography, monospaced financial values, status never by color alone.

**Section 19 — Specialist Council:** Per specialist: ID, name, version, initial/final vote, confidence, evidence, weight, historical/regime reliability, objections, debate changes. Show: agreement/disagreement scores, abstentions, missing specialists, failed calls, timeout behavior.

**Section 20 — Hypothesis Governance:** Full lifecycle: PROPOSED → LOCKED → TESTING → ADVERSARIAL REVIEW → VALIDATED or REJECTED → PROMOTED, ARCHIVED, or RETIRED. Hypothesis cannot be rewritten after results without a new version.

**Section 21 — Browser Compatibility:** Chrome, Safari, Edge, iOS Safari, mobile monitoring. Verify auth, real-time streams, tables, charts, exports, keyboard nav, session expiration, reconnection, responsive layout.

**Section 23 — 15 Final Acceptance Conditions:**
1. Original 760 requirements returned unchanged, count = exactly 760
2. Every mandatory original requirement passes with genuine evidence
3. Every supplemental requirement returned
4. Every CRITICAL supplemental requirement passes
5. Financial calculations independently reconcile
6. Risk controls proven server-side
7. Concurrency and idempotency tests pass
8. Auth and authorization tests pass
9. Audit-chain integrity passes
10. Genuine backup restoration passes
11. Genuine scheduler→outcome→learning→memory trace passes
12. API, SQL, dashboard, reports, exports reconcile
13. No mocked, fabricated, manually inserted, stale, or reused evidence
14. All regression tests pass

---

## CURRENT VERIFICATION STATUS (Pre-Checklist Work)

### Checklist Part 1 Results (ARCH + DATA + API — 80 items visible)

| Section | Complete | Partial | Not Impl / No Suite |
|---|---|---|---|
| ARCH-001–030 | 23 | 0 | 7 (no automated test suite) |
| DATA-001–030 | 30 | 0 | 0 |
| API-001–020 | 13 | 5 | 2 |
| **Total** | **66** | **5** | **9** |

**DATA: 30/30 COMPLETE**  
**ARCH: 23/30 COMPLETE** (7 items require automated test suite — separate project)  
**API: 13/20 COMPLETE** (5 partial, 2 deferred: sorting, full RBAC)

### Key Completed Work
- `src/components/data-footer.tsx` — SOURCE/FETCHED/MODE/PERIOD on all 13 pages
- `src/hooks/use-api.ts` — `lastUpdated: Date | null` exposed
- `vite.config.ts` — PORT/BASE_PATH optional during build (ARCH-030)
- All 13 pages — DataFooter added with real source table names
- Scheduler.tsx — hardcoded `274` fallback removed
- Production build: EXIT_CODE 0 | TypeScript: EXIT_CODE 0
- Bundle inspected: 760KB, no prohibited strings

### Honest Deferred Items
- No automated regression test suite (ARCH-024–029)
- No `/api/v1/` URL prefix (API-002)
- No OpenAPI YAML spec (API-003)
- RBAC single admin token only — 7 roles deferred (API-007)
- No cursor pagination (API-011)
- No sort parameters (API-013)

---

## WHAT HAPPENS NEXT

1. **Receive the 760-item checklist** (7 files) — this is the controlling verification authority
2. **Work through all 760 items** with genuine reproducible evidence per item
3. **Apply all overlays** from documents 3, 4, and 5 simultaneously
4. **Build the terminal** according to document 1, only after backend verification passes
5. **Return FINAL PASS** only when all 15 acceptance conditions from document 5 §23 are satisfied

**Current status: NOT COMPLETE — FINAL PASS PROHIBITED**  
*Reason: 760-item checklist not yet received; full verification not yet performed.*
