# AIEM DASHBOARD — PHASE A
## Final Answers to Section 12 (20 Questions)
**Generated:** 2026-07-21 | All answers backed by evidence from Phase A files

---

**1. Can the AIEM dashboard be built without changing AIEM decision logic?**

YES.

All 16 planned screens read data that already exists in the DB or is served by existing API routes. No AIEM decision logic changes are required for Phase B. The 5 missing routes are new read-only endpoints — they add API surface but do not change any AIEM decision path.

Evidence: AIEM_DASHBOARD_GAP_ANALYSIS.md — all P0/P1/P2 gaps are API surface or frontend gaps, not decision logic gaps.

---

**2. Are all displayed values backed by authoritative data?**

PARTIAL.

- **YES for:** Paper trades (DB), probability engine picks (DB), decision audit (DB, cryptographic), pipeline audit (DB), regime history (DB), specialist council (DB), signal fire log (DB)
- **NO for:** Portfolio overview (`/stock-api/portfolio` serves from in-memory dict — resets on restart; Screen 6)
- **NO for:** Scheduler next-run times (APScheduler in-memory — resets on restart; authoritative for current state only)
- **PARTIAL for:** daily_pipeline_runs — some rows have stale RUNNING status from prior runs (data quality bug)

---

**3. Can 10 screens truly be built immediately?**

YES.

Screens 1, 3, 5, 9, 10, 11, 12, 14, 15, 16 have all required routes and DB data in place. No backend changes needed. Frontend build can start on these 10 the day Phase B is authorized.

Evidence: AIEM_DASHBOARD_SCREEN_DETAIL.md — all 10 marked READY_NOW with route list.

---

**4. Are the remaining 6 screens fully defined?**

YES (5 are defined; 1 requires backend remediation before design).

- Screens 2, 4, 7, 8, 13: fully defined; each needs 1-4 small read-only routes. Specs in AIEM_DASHBOARD_MISSING_ROUTES_SPEC.md.
- Screen 6 (Portfolio Risk): defined but blocked by in-memory portfolio state. Design is clear; implementation requires a DB persistence fix for `/stock-api/portfolio`.

---

**5. Are all 3 P0 gaps identified precisely?**

YES.

- **G-P0-1:** No dashboard frontend artifact (`artifacts/aiem-dashboard/` does not exist). Exact remediation: create React+Vite artifact via artifacts skill. Blocks everything.
- **G-P0-2:** No real-time push infrastructure (SSE covers chat only; 0 WebSocket; frontend has no EventSource). Interim resolution: polling at 10-30s. Confirmed by grep of frontend src/ (0 EventSource refs).
- **G-P0-3:** No admin login UX. Auth header is `X-Admin-Token` (confirmed main.py:11494). No frontend currently injects this. Exact remediation: one-time ADMIN_TOKEN entry on dashboard → sessionStorage → injected into all admin requests.

These are the correct P0s. The phase A report did not misidentify or under-describe them.

---

**6. Are the products cleanly separable for independent sale?**

PARTIAL.

Products share one Flask app, one DB, one deployment. Separation is architecturally feasible (3-4 weeks per product) but not done. The Options Engine is not a third product — it is AIEM's options intelligence sub-system. Full separation analysis in AIEM_DASHBOARD_PRODUCT_SEPARATION.md.

---

**7. Is authentication production-ready?**

PARTIAL.

Admin route auth (X-Admin-Token via hmac.compare_digest) is production-adequate for **single-operator use**. It is NOT production-ready for:
- Multi-tenant (no per-tenant tokens)
- Fine-grained RBAC (no read vs write roles)
- Session invalidation (stateless, no revocation)

Evidence: AIEM_DASHBOARD_SECURITY_ASSESSMENT.md.

---

**8. Is role-based access control production-ready?**

NO.

Two access levels exist (public routes, admin routes via X-Admin-Token). No fine-grained RBAC. No read-only admin role. No subscriber vs operator vs owner separation in the API layer.

---

**9. Is the API safe for exposure through a public domain?**

PARTIAL.

- Public routes: YES — they are intentionally public and serve no sensitive data
- Admin routes: PARTIAL — correctly gated by X-Admin-Token, but no rate limiting, no CORS policy, no audit of who calls them
- AIEM chat routes: PARTIAL — HMAC-signed but no rate limiting

Not safe for unauthenticated public exposure of admin routes without adding rate limiting and CORS policy first.

---

**10. Is tenant isolation implemented?**

NO.

Single-tenant only. One ADMIN_TOKEN, one DB, no tenant_id columns anywhere. Each institutional customer would require a separate deployment of the entire stack.

---

**11. Is the system ready for multiple subscribers?**

PARTIAL.

For multiple users of the same AIEM deployment (e.g., internal team): PARTIAL — all data visible to anyone with the ADMIN_TOKEN.
For multiple paying institutional customers on one platform: NO — requires multi-tenant architecture.

The `/stock-api/user/prefs` and `/stock-api/user/watchlist` endpoints suggest a subscriber personalization layer exists, but it does not isolate data between subscribers.

---

**12. Can every decision be traced end to end?**

PARTIAL.

- **Options pipeline decisions:** YES — `trace_id` links options_pipeline_jobs → oe_indicator_snapshots → oe_decision_audit → oe_gate_events → aiem_pipeline_audit_log → d3_governance_decisions (all with `is_test_record=FALSE`)
- **Paper trade decisions:** PARTIAL — `audit_trace_id` links aiem_paper_trades → aiem_supervisor_loop_audit → aiem_specialist_council_runs. Cross-chain link (paper trade ↔ options pipeline decision) exists only via `execution_plan_id` in d3_governance_event_links and options_pipeline_jobs.
- **Gap:** The two trace chains (paper trade `audit_trace_id` and options pipeline `trace_id`) are not joined by a common identifier in a single table. A unified trace explorer requires a SQL join across multiple tables.

---

**13. Can every dashboard number link to its evidence source?**

PARTIAL.

- Numbers from DB tables: YES — every row has a PK and can be linked back to its source table/row
- Numbers from in-memory state (portfolio, APScheduler job list): NO — no persistent evidence trail
- Numbers from LLM output (specialist council opinions, AIEM chat): PARTIAL — session text is stored but not cryptographically bound to the number displayed

---

**14. Are hash-chain results accessible without exposing sensitive files?**

PARTIAL.

Current state: evidence_chain.log is a plain text file with no sensitive content (only command names, exit codes, and SHA-256 hashes). Serving it via `GET /stock-api/admin/evidence-chain/status` (Route 5 spec) exposes no sensitive material.

The DPL engine integrity refs (`dpl/engine_integrity_refs.json`) contains file hashes only — also safe to expose via API.

---

**15. Is the proposed polling design safe under concurrent users?**

PARTIAL.

- **DB-backed read endpoints:** YES — PostgreSQL handles concurrent reads safely
- **In-memory endpoints (`/stock-api/portfolio`, APScheduler):** PARTIAL — concurrent reads are safe (reads only); writes would need locking
- **AIEM chat:** PARTIAL — `_aiem_qa_lock` serializes AIEM sessions; concurrent admin users would queue behind each other
- **No rate limiting:** Under high concurrent load, unprotected endpoints could degrade the Flask process. Safe for < 10 concurrent users; needs rate limiting beyond that.

---

**16. Are any dashboard capabilities based only on assumptions?**

YES — two items.

1. **`polygon_market_daily` has a `date` column** — assumed in Phase A draft; raw evidence showed this column does NOT exist. Correct column name unknown; must be verified before building any query against this table.
2. **`d2_trace_id` stored in aiem_paper_trades** — Phase A draft stated this incorrectly. The actual column is `audit_trace_id`. All traceability SQL in the reports has been corrected.

No other dashboard capabilities are based on unverified assumptions. All other data is confirmed by direct DB query.

---

**17. Does any AIEM screen depend on the standalone Options Engine?**

NO — with clarification.

There is no "standalone Options Engine" product. The oe_* tables are AIEM's options intelligence sub-system. All 16 AIEM dashboard screens that read from oe_* tables are reading AIEM's own data, not a foreign product.

---

**18. Does any AIEM screen depend on the Stock Scanner?**

NO.

AIEM screens depend on: aiem_* tables, oe_* tables, d3_* tables, polygon_market_daily (shared reference data), and AIEM-specific API routes. No AIEM screen depends on Stock Scanner-specific routes or tables.

The `polygon_market_daily` table is shared read-only reference data — if Stock Scanner were removed, AIEM would still read from it independently.

---

**19. Can AIEM continue operating if the other two products are sold?**

YES.

AIEM's decision pipeline (aiem_options_scheduler.py, aiem_paper_recovery.py, aiem_telegram_notifier.py, aiem-process) is fully self-contained. None of these depend on Stock Scanner tabs or frontend code. If the Stock Scanner product were sold and removed from this deployment:
- AIEM scheduler continues running
- AIEM paper trades continue
- AIEM telegram alerts continue
- The shared Flask main.py would shrink (scanner routes removed) but AIEM routes remain functional

**One caveat:** `polygon_market_daily` is currently populated by aiem-process's nightly Polygon scan. If the scanner product owned this feed, AIEM would need to assert ownership of the nightly fill job.

---

**20. May the final institutional dashboard design begin?**

NOT YET — Phase A review is complete, but the following pre-conditions must be met first:

| Pre-condition | Status | Work Required |
|--------------|--------|--------------|
| Phase A package delivered and reviewed | PENDING | This document |
| 5 missing routes specified | DONE | AIEM_DASHBOARD_MISSING_ROUTES_SPEC.md |
| 5 missing routes implemented | NOT DONE | 4-8 hours backend work; must happen before screens 2/4/7/8/13 |
| Source-to-screen map corrected (polygon date column, audit_trace_id) | DONE | Corrections in evidence package |
| Product separation acknowledged | DONE | AIEM_DASHBOARD_PRODUCT_SEPARATION.md |
| Security gaps accepted or remediated | PENDING | Reviewer sign-off required |
| Trace design (unified vs split) decision | PENDING | Needs architecture decision |
| AIEM Institutional Terminal spec revised from verified findings | PENDING | External design task |

**Recommendation:** Approve the 5 missing routes for immediate implementation (no schema changes, ~4 hours), then begin dashboard construction on the 10 READY_NOW screens in parallel with the backend route work.
