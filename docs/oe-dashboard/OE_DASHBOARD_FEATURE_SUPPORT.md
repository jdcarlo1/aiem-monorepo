# Options Engine Dashboard — Differentiating Features Data Support
**Generated:** 2026-07-30 | All values from raw DB queries — no fabrication

---

## Feature 1: Live Decision Lineage
**Visual trace: candidate → data guard → risk gate → position sizing → execution**

### Data available

| Table | Prod Rows | Covers |
|-------|-----------|--------|
| `options_pipeline_jobs` | 48 (DONE=10, FAILED=37, NO_TRADE_GATES=1) | Pipeline job status per ticker/scan_date |
| `oe_decision_audit` | **15 prod** (is_test_record=FALSE) | Full 6-layer decision record with JSONB payloads |
| `oe_gate_events` | **3 prod** | Gate enforcement events (all ENGINE_INTEGRITY BLOCKED, 2026-07-21 only) |
| `d3_governance_decisions` | **117** | G0-G5 checkpoint decisions per trace |
| `d3_governance_event_links` | **3,058 prod** | Cross-system trace linkage |

### Critical gap: trace_id format mismatch

```
options_pipeline_jobs.trace_id:   e5fbbea92b7e4446   (16-char hex)
oe_decision_audit.decision_id:    0059a45a1139415d905ecfde  (24-char hex)
```

LEFT JOIN on `decision_id = trace_id` returns NULL for all 10 DONE pipeline jobs. The two systems write different ID formats. The join path from a completed pipeline job to its `oe_decision_audit` entry is currently broken.

**Build verdict:** Can visualise the governance/gate chain (d3_governance_decisions, d3_governance_event_links). Cannot build the pipeline-job-to-decision-audit connector until the trace_id format mismatch is diagnosed. Mark this connector as "pending investigation" in the initial build — do not fabricate a join.

---

## Feature 2: "Why This Trade" Panel
**Real gating logic + probability/EV that produced the decision**

### Data available

**oe_options_metrics (98 rows) — confirmed columns:**
```
delta, gamma, theta, vega, rho, vanna, charm, vomma, speed, color, ultima
ev, pop, return_on_risk, premium_at_risk, capital_requirement
max_profit, max_loss, breakeven
iv, iv_rank, iv_percentile, hv_20d, realized_vol, vrp
pc_skew_pp, pc_skew_tag, term_ratio, term_tag
vol_oi_ratio, fill_probability, slippage_pct
```

**oe_indicator_snapshots (3,920 rows) — confirmed columns:**
```
canonical_id, raw_value, normalized_value, signal_direction
confidence, contribution_score, weight, regime_context
quality_status, supported_decision
```

**oe_decision_audit (15 prod rows) — 6 JSONB decision layers:**
```
identity_json, technical_json, options_intel_json, probability_risk_json, justification_json
(plus one more unnamed layer per the column list)
```

### Routes needed (both missing):
1. `GET /stock-api/admin/options-metrics?trace_id=` — returns full greeks + EV + POP for a decision
2. `GET /stock-api/admin/indicator-snapshots?trace_id=` — returns all 79 indicator readings for a decision with contribution scores

**Build verdict:** Data fully supports this panel. Two routes must be added first. 15 prod decision_audit rows is sparse but real.

---

## Feature 3: Live Probability Calibration Reliability Diagram
**Sourced from real calibration data**

### Data available

```
aiem_probability_engine_predictions: 24 rows, all pit_status=pit_safe
  — 16 rows have outcome_ret_1d/2d/3d/4d resolved
  — 8 rows pending outcome resolution

calibrated_horizon_1d.pkl  (2026-07-02):  method=platt, raw_brier=0.2938, cal_brier=0.3720  ← cal WORSE
calibrated_horizon_2d.pkl  (2026-07-02):  method=platt, raw_brier=0.2683, cal_brier=0.3998  ← cal WORSE
calibrated_horizon_3d.pkl  (2026-07-02):  method=platt, raw_brier=0.2638, cal_brier=0.5666  ← cal WORSE (severe)
calibrated_horizon_4d.pkl  (2026-07-02):  method=platt, raw_brier=0.3000, cal_brier=0.4762  ← cal WORSE

Route: GET /stock-api/aiem-probability-engine/calibration  (line 50884, public, no auth)
  — calls pit_metrics.run_pit_metrics() directly (pure DB reads + sklearn.metrics)
  — loads all 4 pkl artifacts
  — returns contaminated/corrected/genuine PIT buckets
```

### Honest-display requirement

The Platt scaling made calibration worse across all 4 horizons (cal_brier > raw_brier for all). The uncalibrated raw model is more accurate by Brier score. This means:
- The dashboard **must not** label the pkl-calibrated outputs as "improved" or as the accuracy track record.
- The reliability diagram must use the `genuine` (pit_safe) bucket from `aiem_probability_engine_predictions`, not the pkl-adjusted outputs.
- Both raw and calibrated Brier must be visible with clear labelling.

**Build verdict:** Real data exists. Reliability diagram is buildable. Calibration quality disclosure is mandatory — not optional.

---

## Feature 4: Evidence-Chain / Verification Status Indicator
**Sealed/verified/sha256-anchored status on live decisions**

### Data available

```
oe_decision_audit.verification_status values (prod rows only):
  VERIFIED: 13
  PENDING:  2
  (REPLAY_ERROR, CODE_DRIFT, TAMPERED exist only in is_test_record=TRUE rows)

oe_gate_events columns confirmed:
  live_hash, expected_hash, chain_hash, prev_hash, event_hash, git_commit

evidence_chain.log: artifacts/stock-scanner-api/evidence_chain.log (SEQ chain)
evidence_chain.jsonl: same directory, JSONL format
Route: GET /stock-api/admin/evidence-chain/status (line 72180, ADMIN)
```

**Build verdict:** Data fully supports this feature. Can show VERIFIED/PENDING/SEALED status badge per decision. Small prod dataset (15 rows) limits how many decisions show verified status on day 1, but the chain infrastructure is real.

---

## Summary

| Feature | Data Supports Build? | Condition |
|---------|---------------------|-----------|
| Live decision lineage | PARTIAL | governance chain buildable; pipeline-job connector blocked by trace_id format mismatch |
| "Why this trade" panel | YES | 2 routes must be added first |
| Calibration reliability diagram | YES | must disclose cal_brier > raw_brier; use genuine pit_safe bucket |
| Evidence-chain status indicator | YES | no conditions |
