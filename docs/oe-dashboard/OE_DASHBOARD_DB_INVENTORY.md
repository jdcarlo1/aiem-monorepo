# Options Engine Dashboard — DB Inventory
**Generated:** 2026-07-30 | **Source:** SELECT COUNT(*) per table + information_schema.columns

---

## Tier 1: Core Options Pipeline (ACTIVE_AND_POPULATED)

| Table | Rows | Date Range | Key Columns (dashboard-relevant) |
|-------|------|------------|----------------------------------|
| `oe_decision_audit` | 361 total / **15 prod** | 2026-07-19 → 2026-07-25 | decision_id (24-char hex), parent_id, input_hash, output_hash, verification_status (VERIFIED/PENDING/REPLAY_ERROR/CODE_DRIFT/TAMPERED), is_test_record, identity_json, technical_json, options_intel_json, probability_risk_json, justification_json (JSONB) |
| `oe_gate_events` | 4 total / **3 prod** | 2026-07-21 only | gate_event_id, gate_name, fired_at, ticker, trace_id, live_hash, expected_hash, chain_hash, prev_hash, event_hash, action_taken (all=BLOCKED), is_test_record, git_commit |
| `oe_indicator_snapshots` | **3,920** | — | id, trace_id, ticker, scan_date, canonical_id, raw_value, normalized_value, signal_direction, confidence, contribution_score, weight, freshness_seconds, quality_status, supported_decision, regime_context, captured_at |
| `oe_indicator_registry` | **79** | 2026-07-20 | — |
| `oe_strategy_registry` | **42** | — | strategy_id, name, family, direction, call_put_type, risk_profile, max_legs, defined_risk, enabled |
| `oe_pattern_snapshots` | **237** | — | trace_id, ticker, scan_date, canonical_id, timeframe, detection_confidence, actionable, influenced_recommendation, outcome, mfe_pct, mae_pct |
| `oe_options_metrics` | **98** | — | trace_id, alert_id, ticker, scan_date, direction, strike, expiry, dte, bid, ask, mid, last_price, spread_pct, volume, open_interest, vol_oi_ratio, iv, iv_rank, iv_percentile, hv_20d, realized_vol, vrp, pc_skew_pp, pc_skew_tag, term_ratio, delta, gamma, theta, vega, rho, vanna, charm, vomma, speed, color, ultima, gex_m, gex_regime, ev, pop, return_on_risk, premium_at_risk, capital_requirement, max_profit, max_loss, breakeven, fill_probability, slippage_pct, outcome, pnl_pct |
| `oe_scheduler_trace` | **175** | — | trace_id, stage_name, stage_seq, recorded_at, ticker, scan_date, fire_timestamp, worker_pid, job_id, completion_status, failure_reason, stage_metadata, is_test_record |
| `oe_legacy_replay_exceptions` | **325** | — | decision_id, replayability_status, eligible_for_*, root_cause, ticker, scan_date, trace_id |
| `oe_trade_records` | **28** | 2026-07-23 → 2026-07-28 | alert_id, trace_id, ticker, scan_date, strategy_family, direction, legs_json, entry_ts, entry_price, exit_ts, exit_price, quantity, fees_est, slippage_est, premium_paid_received, capital_reserved, max_risk, max_reward, entry_greeks_json, exit_greeks_json, entry_iv, exit_iv, underlying_price_path, option_price_path, mfe_pct, mae_pct, realized_pnl, unrealized_pnl_path, return_pct, return_on_risk, holding_days, exit_reason, fill_quality, portfolio_state_json |
| `oe_classification_correction_ledger` | **15,505** | 2026-07-20 → 2026-07-30 | — |
| `oe_incidents` | **94** | 2026-07-18 → 2026-07-30 | incident_ts, ticker, scan_date, failure_source, failure_type (UNKNOWN_OPERATIONAL=91, MISSING_DATA=3), classification, error_text, remediation, resolved |
| `oe_decision_snapshots` | **62** | 2026-07-19 → 2026-07-25 | — |
| `oe_decision_replay_inputs` | **332** | 2026-07-19 → 2026-07-25 | — |
| `oe_known_synthetic_rows` | **10** | 2026-07-19 | — |
| `oe_index_corrections` | **62** | 2026-07-19 → 2026-07-25 | — |

---

## Tier 2: Pipeline Orchestration

| Table | Rows | Date Range | Key Columns |
|-------|------|------------|-------------|
| `options_pipeline_jobs` | **48** | 2026-07-15 → 2026-07-30 | ticker, scan_date, status (FAILED=37, DONE=10, NO_TRADE_GATES=1), claim_id, trace_id (16-char hex — different format from oe_decision_audit), alert_id, direction, selected_score, trigger_source, chain_hash, completed_at |
| `daily_pipeline_runs` | **16** | — | run_date, trigger_source, status, candidates_seeded, candidates_executed, candidates_no_trade, started_at, completed_at |
| `options_structure_scan` | **886** | — | ticker, scan_date, spot, gex_m, gex_regime, gamma_flip_price, pc_skew_pp, pc_skew_tag, term_ratio, front_iv, back_iv |
| `aiem_options_alerts` | **25** | 2026-07-16 → 2026-07-17 | ticker, direction, alert_date, selected_score, audit_chain_sha256, delta_val, iv_val, expected_return, outcome_status |
| `aiem_options_alert_snapshots` | **25** | 2026-07-28 | — |
| `options_engine_mtf` | **40** | 2026-07-17 → 2026-07-30 | — |
| `options_engine_premarket` | **1** | 2026-07-23 | — |
| `options_engine_runs` | **2** | 2026-07-28 | — |
| `oe_portfolio_context` | **25** | 2026-07-16 → 2026-07-18 | — |

---

## Tier 3: Governance

| Table | Rows | Key Columns |
|-------|------|-------------|
| `d3_governance_decisions` | **117** (prod: G0/ALLOW=38, G2/ALLOW=28, G3/ALLOW=23, G1/ALLOW=10, G3/BLOCK=5, G5/ALLOW=4, G0/BLOCK=3) | governance_decision_id, trace_id, checkpoint, decision, blocking, reason_codes, decision_hash, is_test_record |
| `d3_governance_event_links` | **3,058 prod** | governance_decision_id, candidate_id, ticker, decision_id, execution_plan_id, paper_trade_id, check_result, enforcement_action, input_hash, output_hash, previous_event_hash, event_hash, source_code_commit |

---

## Tier 4: Calibration

| Table | Rows | Notes |
|-------|------|-------|
| `aiem_probability_engine_predictions` | **24** (all pit_safe, 16 with outcomes) | prob_up_1d/2d/3d/4d, confidence, pit_status, outcome_ret_1d/2d/3d/4d |
| `aiem_probability_engine_daily_picks` | **14** | rank, score, prob_up_1d/2d/3d/4d, edge_after_cost_prob_pts, regime_tag |
| `aiem_probability_engine_live_queries` | **50** | ticker, mode, pit_status, verified |

**Calibration pkl files** (all at `aiem_probability_engine/models/`, 2026-07-02):

| Horizon | Method | raw_brier | cal_brier | Note |
|---------|--------|-----------|-----------|------|
| 1d | platt | 0.2938 | 0.3720 | cal_brier > raw — Platt made it worse |
| 2d | platt | 0.2683 | 0.3998 | cal_brier > raw — Platt made it worse |
| 3d | platt | 0.2638 | 0.5666 | cal_brier > raw — severe degradation |
| 4d | platt | 0.3000 | 0.4762 | cal_brier > raw — Platt made it worse |

n_train=80-119, n_val=39-93, n_test=117-164 per horizon. Calibration set was too small.

---

## Tier 5: Paper Trades (options-adjacent)

| Table | Rows | Notes |
|-------|------|-------|
| `aiem_paper_trades` | **35** | Includes options fields: direction, strike, expiry, probability_score, market_regime, volatility_regime |
| `aiem_position_sizing_log` | **264** | conviction_score, entry_price, calculated_stop_price, gate_result |

---

## Tier 6: Empty / Not Yet Active

| Table | Rows | Notes |
|-------|------|-------|
| `oe_attribution_runs` | 0 | Champion-challenger attribution — no runs yet |
| `oe_challenger_decisions` | 0 | — |
| `oe_regime_performance` | 0 | — |
| `oe_strategy_candidates` | 0 | — |
| `oe_strategy_scorecards` | 0 | — |
| `oe_counterfactual_outcomes` | 0 | — |
| `oe_counterfactual_snapshots` | 0 | — |
| `oe_interaction_hypotheses` | 0 | — |
| `oe_interaction_results` | 0 | — |
| `oe_indicator_attribution` | 0 | — |

---

## Job Heartbeats (options pipeline)

| job_name | last_success | consecutive_failures |
|----------|-------------|----------------------|
| options_pipeline_scheduler | 2026-07-30 21:55:31 | 0 |
| aiem_independent_options_scan | 2026-07-30 14:20:00 | 0 |
