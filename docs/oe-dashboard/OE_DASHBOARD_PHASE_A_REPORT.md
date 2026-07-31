# Options Engine Dashboard — Phase A Final Report
**Generated:** 2026-07-30 | **Status:** COMPLETE | **Scope:** Read-only inventory — no code changes

---

## What Was Inventoried

| Artifact | Count | Method |
|----------|-------|--------|
| oe_* tables (total) | 38 | pg_tables query |
| oe_* tables populated (>0 rows) | 26 | SELECT COUNT(*) per table |
| options-adjacent tables (pipeline, calibration, paper) | 14 | same |
| API routes relevant to OE dashboard | 16 existing + 4 missing | grep -n @app.route |
| Differentiating features with real data support | 4 of 4 (2 full, 2 partial) | raw DB query |
| SSE endpoints (options pipeline) | 0 existing — must build | grep text/event-stream |
| Calibration pkl artifacts | 4 of 4 exist | ls -la |

---

## Dashboard Screen Readiness

| Screen | API Routes Ready | DB Data Exists | Missing Routes | Real-Push Ready | Launch-Ready? |
|--------|-----------------|----------------|----------------|-----------------|--------------|
| Live Decisions | PARTIAL | YES | 0 | NO (SSE must build) | PARTIAL — SSE gap |
| Decision Proof | YES | PARTIAL | 0 | N/A | PARTIAL — only 15 prod audit rows |
| Positions & P&L | NO | YES | 2 (options_metrics, trade_records) | N/A | NO — routes missing |
| Calibration | YES | YES (sparse) | 0 | N/A | YES (honest disclosure required) |
| System Status | YES | YES | 0 | N/A | YES |

---

## Differentiating Features — Data Support Verdict

### 1. Live decision lineage
**Verdict: PARTIAL**
- `oe_decision_audit`: 15 prod rows (VERIFIED=13, PENDING=2). 346 test rows excluded.
- `oe_gate_events`: 3 prod rows — all ENGINE_INTEGRITY BLOCKED, 2026-07-21 only.
- `d3_governance_decisions`: 117 rows — G0/ALLOW=38, G2/ALLOW=28, G3/ALLOW=23, G1/ALLOW=10, G3/BLOCK=5, G5/ALLOW=4, G0/BLOCK=3.
- `d3_governance_event_links`: 3,058 prod rows — links governance cycles to decisions, execution plans, paper trades.
- **Gap:** `options_pipeline_jobs` DONE trace_ids (e.g., `e5fbbea92b7e4446`, 16-char hex) do NOT match `oe_decision_audit` decision_ids (24-char hex, e.g., `0059a45a1139415d905ecfde`). The two have different ID formats — LEFT JOIN on `decision_id = trace_id` returns NULL for all 10 DONE jobs. Lineage trace is broken at the pipeline-job → decision-audit connector. Must be resolved before building the lineage view connector.

### 2. "Why this trade" panel
**Verdict: YES — 2 routes must be added first**
- `oe_options_metrics`: 98 rows. Columns confirmed present: `delta, gamma, theta, vega, rho, vanna, charm, vomma, speed, color, ultima, ev, pop, return_on_risk, breakeven, max_profit, max_loss, premium_at_risk, capital_requirement, iv, iv_rank, iv_percentile, realized_vol, vrp`. NO API ROUTE.
- `oe_indicator_snapshots`: 3,920 rows. Columns include `canonical_id, raw_value, normalized_value, confidence, contribution_score, weight, regime_context`. NO ROUTE by trace_id.
- `oe_decision_audit`: 6 JSONB layers per row: `identity_json, technical_json, options_intel_json, probability_risk_json, justification_json` + one more. Route exists at `/stock-api/admin/decision-audit` but only 15 prod rows.

### 3. Live probability calibration reliability diagram
**Verdict: YES — data is real, calibration quality is poor, must disclose honestly**
- 4 calibrator pkl files exist: `calibrated_horizon_{1-4}d.pkl` (2026-07-02 00:22, Platt scaling).
- Raw pkl data: `1d: raw_brier=0.294 cal_brier=0.372` | `2d: raw_brier=0.268 cal_brier=0.400` | `3d: raw_brier=0.264 cal_brier=0.567` | `4d: raw_brier=0.300 cal_brier=0.476`
- **cal_brier > raw_brier for all 4 horizons** — Platt scaling made calibration worse. The uncalibrated model is more accurate by Brier score.
- `aiem_probability_engine_predictions`: 24 rows (all `pit_safe`), 16 have outcomes.
- `/stock-api/aiem-probability-engine/calibration` endpoint exists and calls `pit_metrics.run_pit_metrics()` directly.
- **Requirement:** Dashboard must show raw Brier scores as the honest track record. Cal scores must be labelled and not presented as improved calibration. Reliability diagram can be built from real data.

### 4. Visible evidence-chain/verification status indicator
**Verdict: YES**
- `oe_decision_audit.verification_status` col: VERIFIED/PENDING/REPLAY_ERROR/CODE_DRIFT/TAMPERED (prod: 13 VERIFIED + 2 PENDING).
- `oe_gate_events`: has `chain_hash`, `prev_hash`, `event_hash` per gate event.
- `/stock-api/admin/evidence-chain/status` endpoint exists @ line 72180.
- `evidence_chain.log` file: `artifacts/stock-scanner-api/evidence_chain.log` (also `evidence_chain.jsonl`).

---

## Top Gaps to Close Before Phase B Starts

| Priority | Item | Resolution | Effort |
|----------|------|------------|--------|
| P0 | No SSE endpoint for options pipeline events | Add `/stock-api/admin/options-pipeline/stream` — SSE consumer on `aiem_communication_bus.py`; bus already exists and writes to `aiem_bus_transfer_log` | Medium |
| P0 | No dashboard artifact | Create `artifacts/oe-dashboard/` React+Vite | Large (Phase B) |
| P1 | No route for `oe_options_metrics` | Add `GET /stock-api/admin/options-metrics?trace_id=&date=` | Small |
| P1 | No route for `oe_trade_records` | Add `GET /stock-api/admin/trade-records?date=&limit=` | Small |
| P1 | No route for `oe_indicator_snapshots` by trace | Add `GET /stock-api/admin/indicator-snapshots?trace_id=` | Small |
| P2 | trace_id format mismatch (pipeline jobs → decision audit) | Investigate join path before building lineage connector | Investigation |
| P2 | No route for `oe_scheduler_trace` | Add `GET /stock-api/admin/scheduler-trace?date=` | Tiny |

---

## Auth Model

```
Header: X-Admin-Token: <ADMIN_TOKEN>
Check:  _admin_ok() helper at main.py line 231, 253
Method: hmac.compare_digest(want, provided) where want = os.environ.get("ADMIN_TOKEN","")
Fail:   returns {"error": "unauthorized"} HTTP 401
Scope:  all /stock-api/admin/* routes
Public: /stock-api/options-pipeline/candidates uses same _admin_ok() — effectively admin
Public (no auth): /stock-api/quant/options-probability, /stock-api/options/reconcile,
                  /stock-api/aiem-probability-engine/calibration|daily-picks|track-record
```

---

## BUILD MAY BEGIN: YES

**Conditions before starting:**
1. 3 routes must be added before Positions & P&L and "Why this trade" screens can be built (oe_options_metrics, oe_trade_records, oe_indicator_snapshots by trace_id). These are the first Phase B backend tasks.
2. SSE endpoint for options pipeline must be wired first (before Live Decisions screen). `aiem_communication_bus.py` infrastructure already exists.
3. Lineage view trace_id join format mismatch must be investigated before building the lineage connector line in the decision flow diagram. All other lineage data (d3_governance_decisions, d3_governance_event_links) is available.
4. Calibration screen must display raw Brier scores, not pkl-calibrated scores. pkl-calibrated Brier is worse than raw for all 4 horizons.

**Phase A produced zero code changes. All evidence backed by raw DB queries and file inspection.**
