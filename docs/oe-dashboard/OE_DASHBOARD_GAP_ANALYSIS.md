# Options Engine Dashboard — Gap Analysis
**Generated:** 2026-07-30

---

## Summary

| Priority | Count |
|----------|-------|
| P0 (blocking launch) | 2 |
| P1 (required for specific screens) | 3 |
| P2 (investigation / deferred) | 2 |

---

## P0 — Blocking Launch

### G-P0-1: No OE dashboard frontend artifact
**Missing:** No React+Vite artifact exists for the Options Engine terminal. `artifacts/aiem-dashboard/` is the separate AIEM Institutional Terminal — different product.
**Resolution:** Create `artifacts/oe-dashboard/` as a new React+Vite artifact with ADMIN_TOKEN header injection.
**Blocked by:** Nothing.
**Effort:** Large (Phase B main task).

### G-P0-2: No SSE endpoint for options pipeline events
**Missing:** The live-decisions screen requires real push (directive is explicit: "polling is not acceptable for this screen"). Only one SSE endpoint exists in the entire server: `/stock-api/aiem/chat/stream` (line 69538, chat only). There is no WebSocket library in the server. No SSE stream exists for pipeline job state changes, gate events, or decision completions.
**Infrastructure that exists:** `aiem_communication_bus.py` is a working synchronous in-process event bus that writes all stage transitions to `aiem_bus_transfer_log` DB table. A new SSE endpoint can consume this bus.
**Resolution:** Add `GET /stock-api/admin/options-pipeline/stream` — SSE endpoint that polls `aiem_bus_transfer_log` for new events since last-seen-id and streams them as SSE.
**Blocked by:** G-P0-1.
**Effort:** Medium (new endpoint + bus consumer).

---

## P1 — Required for Specific Screens

### G-P1-1: No route for oe_options_metrics (greeks/EV/POP)
**Missing:** `oe_options_metrics` (98 rows) has full greeks (delta/gamma/theta/vega/rho/vanna/charm/vomma/speed/color/ultima), EV, POP, return_on_risk, premium_at_risk, max_profit, max_loss, breakeven, iv, iv_rank. No API route exposes this table.
**Screens blocked:** Positions & P&L (Greeks view), "Why this trade" panel.
**Resolution:** `GET /stock-api/admin/options-metrics?trace_id=&date=&limit=`
**Effort:** Small.

### G-P1-2: No route for oe_trade_records
**Missing:** `oe_trade_records` (28 rows, 2026-07-23 to 2026-07-28) has realized_pnl, unrealized_pnl_path, return_pct, entry_greeks_json, exit_greeks_json, underlying_price_path, option_price_path, mfe_pct, mae_pct, exit_reason, fill_quality. No API route.
**Screens blocked:** Positions & P&L (closed trades, P&L history).
**Resolution:** `GET /stock-api/admin/trade-records?date=&ticker=&limit=`
**Effort:** Small.

### G-P1-3: No route for oe_indicator_snapshots by trace_id
**Missing:** `oe_indicator_snapshots` (3,920 rows) has per-indicator contribution_score, weight, and supported_decision per decision trace. No route to query by trace_id.
**Screens blocked:** "Why this trade" panel (indicator contribution breakdown).
**Resolution:** `GET /stock-api/admin/indicator-snapshots?trace_id=`
**Effort:** Small.

---

## P2 — Investigation / Deferred

### G-P2-1: trace_id format mismatch between options_pipeline_jobs and oe_decision_audit
**Finding:** `options_pipeline_jobs.trace_id` is 16-char hex (e.g., `e5fbbea92b7e4446`). `oe_decision_audit.decision_id` is 24-char hex (e.g., `0059a45a1139415d905ecfde`). LEFT JOIN on `decision_id = trace_id` returns NULL for all 10 DONE pipeline jobs. The pipeline-job-to-decision-audit connector is broken.
**Impact:** Decision lineage view cannot show the full trace from a specific pipeline job to its oe_decision_audit record.
**Resolution:** Investigate which column in oe_decision_audit joins to options_pipeline_jobs (possibly `parent_id` or `alert_id`). The governance chain (d3_governance_decisions/d3_governance_event_links) is unaffected — those use their own trace IDs.
**Effort:** Investigation only.

### G-P2-2: No route for oe_scheduler_trace
**Table:** `oe_scheduler_trace` (175 rows) — per-stage pipeline execution trace with timestamps, completion_status, failure_reason, worker_pid.
**Impact:** System Status screen cannot show per-stage execution drill-down.
**Resolution:** `GET /stock-api/admin/scheduler-trace?date=&ticker=`
**Effort:** Tiny.

---

## Screens That Can Launch Without Additional Backend Work

| Screen | Can Launch? | Why |
|--------|------------|-----|
| System Status | YES after P0-1 (artifact) | All routes exist: /health, /admin/job-health, /admin/scheduler-jobs |
| Calibration | YES after P0-1 | /calibration endpoint exists, 4 pkl files exist, 24 predictions |
| Decision Proof | YES after P0-1 | /admin/decision-audit, /admin/gate-events, /admin/evidence-chain/status all exist |
| Live Decisions | After P0-1 + P0-2 | Needs SSE endpoint first |
| Positions & P&L | After P0-1 + P1-1 + P1-2 | Needs 2 routes first |

---

## Phase B Recommended Build Order

1. Create `artifacts/oe-dashboard/` (resolves G-P0-1)
2. Add 3 missing P1 routes to main.py (G-P1-1, G-P1-2, G-P1-3) — total ~4 hours
3. Add SSE endpoint for options pipeline (G-P0-2) — ~1 day
4. Build System Status screen (no dependencies)
5. Build Calibration screen (no dependencies; honest Brier disclosure required)
6. Build Decision Proof screen
7. Build Live Decisions screen (after SSE endpoint live)
8. Build Positions & P&L screen (after P1 routes live)
9. Investigate G-P2-1 (trace_id mismatch) — do not build lineage connector until resolved
