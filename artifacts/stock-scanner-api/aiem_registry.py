"""
aiem_registry.py
-----------------
Formal Module Registry + Tool Registry for the AIEM 18-phase architecture
("AEIM DIAGRAM 2 — MASTER WIRING + VERIFICATION").

This is the shared prerequisite infrastructure requested before Phase 0-17
wiring/verification begins. It does NOT touch any trading logic — it only
records, in structured DB tables, what modules/tools exist, which phase
owns them, and (once each phase is actually proven) the verification
result for that phase.

Ground rules this file follows (per explicit instruction):
  - Auto-generate what is mechanically knowable right now (file existence,
    phase assignment per spec text, tool registration status, exclusion
    status, CLI-vs-AI-tool classification, alias mappings).
  - Never fabricate what is NOT yet knowable (owned_tools per module,
    required_inputs/produced_outputs, upstream/downstream edges,
    verification_result). Those fields start as NULL / 'PENDING_VERIFICATION'
    and are only filled in when a phase is actually proven with real
    logs/commands, per phase-by-phase verification.
  - Canonical single ownership: each module has exactly ONE module_phase.
    Where a module was referenced in more than one phase in the spec, the
    FIRST phase of appearance wins, UNLESS an explicit correction was given
    (daily_loss_limit.py -> Phase 11, execution.py/execution_simulator.py
    -> Phase 13, backtest_*.py -> Phase 7). All such resolutions are
    recorded in `dependency_notes` / `ownership_note`, never silently.

REQUIRES: AIEM_DATABASE_URL.
"""

import os
import json
import datetime as dt

import psycopg2
import psycopg2.extras


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL")
    if not url:
        raise RuntimeError("AIEM_DATABASE_URL is not set.")
    return psycopg2.connect(url)


DDL = """
CREATE TABLE IF NOT EXISTS aiem_module_registry (
    module_id               SERIAL PRIMARY KEY,
    module_name             TEXT UNIQUE NOT NULL,
    module_file             TEXT NOT NULL,
    module_phase            INTEGER NOT NULL,
    module_phase_name       TEXT,
    owned_tools             TEXT[],
    required_inputs         TEXT,
    produced_outputs        TEXT,
    upstream_modules        TEXT[],
    downstream_modules      TEXT[],
    verification_required   BOOLEAN NOT NULL DEFAULT TRUE,
    audit_log_enabled       BOOLEAN,
    execution_status        TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    last_verified_date      TIMESTAMPTZ,
    verified_by_command     TEXT,
    verification_result     TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    verification_version    INTEGER NOT NULL DEFAULT 0,
    ownership_note          TEXT,
    ownership_status        TEXT NOT NULL DEFAULT 'CONFIRMED',
    file_exists_confirmed   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE aiem_module_registry ADD COLUMN IF NOT EXISTS ownership_status TEXT NOT NULL DEFAULT 'CONFIRMED';

CREATE TABLE IF NOT EXISTS aiem_tool_registry (
    tool_id                     SERIAL PRIMARY KEY,
    tool_name                   TEXT UNIQUE NOT NULL,
    owning_module_or_phase      TEXT,
    owning_module               TEXT,
    tool_verification_level     TEXT NOT NULL DEFAULT 'phase_only',
    tool_type                   TEXT NOT NULL DEFAULT 'ai_callable_tool',
    required_inputs             TEXT,
    produced_outputs            TEXT,
    can_run_independently       BOOLEAN,
    requires_market_data        BOOLEAN,
    requires_options_data       BOOLEAN,
    requires_historical_data    BOOLEAN,
    requires_trade_history      BOOLEAN,
    writes_audit_log            BOOLEAN,
    excluded_from_autonomous_use BOOLEAN NOT NULL DEFAULT FALSE,
    exclusion_reason            TEXT,
    alias_of                    TEXT,
    dependency_notes            TEXT,
    registered_in_tool_map      BOOLEAN NOT NULL DEFAULT FALSE,
    verification_status         TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    last_verified_date          TIMESTAMPTZ,
    verified_by_command         TEXT,
    verification_result         TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    verification_version        INTEGER NOT NULL DEFAULT 0,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE aiem_tool_registry ADD COLUMN IF NOT EXISTS owning_module TEXT;
ALTER TABLE aiem_tool_registry ADD COLUMN IF NOT EXISTS tool_verification_level TEXT NOT NULL DEFAULT 'phase_only';
"""


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("[aiem_registry] schema ready (aiem_module_registry, aiem_tool_registry)")


PHASE_NAMES = {
    0: "Scanner Input / Candidate Generation",
    1: "Orchestration Layer",
    2: "Guardrails & Safety",
    3: "Macro & Regime Context",
    4: "Discovery Engine",
    5: "Technical Signal Layer",
    6: "Options & Smart Money Flow",
    7: "Statistical Validation & Backtesting",
    8: "ML / Probability Engine",
    9: "Scoring, Analytics & Decision Logging",
    10: "Specialist Council / Debate",
    11: "Risk Gate & Position Sizing",
    12: "Edge Filter & Exit Engine",
    13: "Execution & Shadow Trading",
    14: "Performance Audit",
    15: "Learning & Adaptation Loop",
    16: "Alerts & Notifications",
    17: "Verification & Observability",
}

# module_file -> (phase, ownership_note or None)
# Canonical phase = FIRST phase of appearance in the spec, EXCEPT the 3
# explicit corrections Joel gave (daily_loss_limit.py, execution.py,
# execution_simulator.py, backtest_*.py), which are hard-set below.
MODULE_PHASE_MAP = {
    # Phase 0
    "scanner.py": 0, "composite_scan.py": 0, "prop_signal.py": 0,
    "precursor_signals.py": 0, "opening_snapshot_tracker.py": 0,
    "premarket_gap_continuation_scanner.py": 0, "premarket_open_trader.py": 0,
    "intraday_continuation_scanner.py": 0, "multiday_runner.py": 0,
    "aiem_probability_engine/daily_picks.py": 0,
    # Phase 1
    "main.py": 1, "aiem_master_orchestrator.py": 1, "aiem_v3_orchestrator.py": 1,
    "self_coding_orchestrator.py": 1, "aiem_process.py": 1, "aiem_v2_system.py": 1,
    "aiem_supervisor.py": 1, "aiem_comm_test.py": 1, "aiem_provenance.py": 1,
    "aiem_intelligence_layer.py": 1, "aiem_level2.py": 1, "aiem_level3.py": 1,
    # Phase 2
    "point_in_time_guard.py": 2, "staleness_guard.py": 2, "lookahead_audit.py": 2,
    "simulation_lock.py": 2, "order_dedup.py": 2, "aiem_isolation_guard.py": 2,
    "aiem_security.py": 2, "kill_switch.py": 2, "manual_rollback.py": 2,
    "shadow_ledger.py": 2,
    # Phase 3
    "aiem_macro_engine.py": 3, "regime.py": 3, "regime_detector.py": 3,
    "regime_monitor.py": 3, "regime_macro_patch.py": 3, "market_regime_overlay.py": 3,
    "macro_cross_asset.py": 3, "fred_macro.py": 3, "economic_calendar.py": 3,
    "sector_etf_data.py": 3, "aiem_module7_sector_rotation.py": 3, "aiem_cta_triggers.py": 3,
    # Phase 4
    "aiem_discovery_engine.py": 4, "aiem_v3_discovery.py": 4, "aiem_module5_discovery.py": 4,
    "aiem_module6_rediscovery.py": 4, "signal_discovery_gp.py": 4,
    "breakout_signature_discovery.py": 4, "behavioral_fingerprint.py": 4,
    "historical_analog_search.py": 4, "niche_segment_finder.py": 4,
    "literature_scanner.py": 4, "hypothesis_registry.py": 4,
    "active_hypothesis_selection.py": 4, "causal_discovery.py": 4,
    "causal_inference.py": 4, "adversarial_critique.py": 4,
    # Phase 5
    "aiem_v3_technical.py": 5, "advanced_quant_indicators.py": 5, "indicators.py": 5,
    "candlestick_patterns.py": 5, "price_structure_patterns.py": 5,
    "vwap_indicators.py": 5, "aiem_momentum_exhaustion.py": 5,
    "aiem_pullback_reentry.py": 5, "aiem_selloff_reversion.py": 5,
    "aiem_short_squeeze.py": 5, "eod_swing.py": 5, "momentum_trade_trainer.py": 5,
    # Phase 6
    "options_sweep.py": 6, "smart_money.py": 6, "smart_money_divergence_detector.py": 6,
    "insider_trades.py": 6, "congress_trades.py": 6, "aiem_options_structure.py": 6,
    "microstructure_proxy.py": 6, "fetch_si_background.py": 6,
    # Phase 7
    "layer9_statistical_edge.py": 7, "stat_arb_engine.py": 7, "volatility_clustering.py": 7,
    "aiem_stat_tests.py": 7, "factors.py": 7, "benchmark_comparison.py": 7,
    "historical_performance.py": 7, "event_study_backtest.py": 7,
    "backtest.py": 7, "backtest_deeper.py": 7, "backtest_options.py": 7,
    "backtest_results.py": 7, "backtest_harness.py": 7, "backtest_tiers.py": 7,
    "backtest_week.py": 7, "backtest_eod_swing.py": 7, "backtest_falsenegatives.py": 7,
    "backtest_losers.py": 7, "backtest_morning_losers.py": 7, "backtest_grinder.py": 7,
    "backtest_grinder_losers.py": 7, "backtest_combo60.py": 7, "backtest_highfactor.py": 7,
    "backtest_quant_vs_v2.py": 7,
    # Phase 8
    "aiem_probability_engine/__init__.py": 8, "aiem_probability_engine/calibration.py": 8,
    "aiem_probability_engine/config.py": 8, "aiem_probability_engine/context.py": 8,
    "aiem_probability_engine/daily_scheduler.py": 8, "aiem_probability_engine/data_snapshot.py": 8,
    "aiem_probability_engine/date_utils.py": 8, "aiem_probability_engine/features.py": 8,
    "aiem_probability_engine/live_query.py": 8, "aiem_probability_engine/model_registry.py": 8,
    "aiem_probability_engine/pit_correction.py": 8, "aiem_probability_engine/pit_metrics.py": 8,
    "aiem_probability_engine/predict.py": 8, "aiem_probability_engine/reports.py": 8,
    "aiem_probability_engine/schemas.py": 8, "aiem_probability_engine/train.py": 8,
    "aiem_probability_engine/verify_live_query.py": 8, "aiem_probability_engine/walk_forward.py": 8,
    "scripts/spy_historical_backfill.py": 8,
    "ml_engine.py": 8, "ml_infrastructure.py": 8, "model_training.py": 8,
    "feature_engineering.py": 8, "alpha_feature_engineering.py": 8,
    "alpha_historical_trainer.py": 8, "alpha_train_pipeline.py": 8,
    "automated_retrain_pipeline.py": 8, "retrain_pipeline.py": 8,
    # Phase 9
    "scoring.py": 9, "analytics.py": 9, "ensemble_combiner.py": 9,
    "pre_recommendation_synthesis.py": 9, "signal_correlation.py": 9,
    "signal_magnitude_analysis.py": 9, "evaluation_metrics.py": 9,
    "evaluation_windows.py": 9, "prediction_logger.py": 9, "decision_logger.py": 9,
    "decision_logging_helper.py": 9,
    # Phase 10
    "specialist_council.py": 10, "bull_bear_debate.py": 10,
    # Phase 11
    "pre_decision_risk_gate.py": 11, "aiem_risk_guards.py": 11,
    "portfolio_correlation_risk.py": 11, "portfolio_allocator.py": 11,
    "portfolio.py": 11, "position_sizing.py": 11, "aiem_position_sizing.py": 11,
    "rl_position_sizer.py": 11, "slippage_model.py": 11,
    "daily_loss_limit.py": 11,  # EXPLICIT CORRECTION (was ref'd in Phase 2 spec text)
    # Phase 12
    "aiem_edge_filter.py": 12, "aiem_exit_engine.py": 12,
    # Phase 13
    "execution.py": 13,            # EXPLICIT CORRECTION (was ref'd in Phase 12 spec text)
    "execution_simulator.py": 13,  # EXPLICIT CORRECTION (was ref'd in Phase 12 spec text)
    "pnl.py": 13, "position_reconciler.py": 13,
    # Phase 14
    "aiem_performance_auditor.py": 14, "aiem_pipeline_audit.py": 14,
    "aiem_process_backtest.py": 14, "signal_outcomes.py": 14,
    # Phase 15
    "aiem_closed_loop_learning.py": 15, "aiem_rl_engine.py": 15,
    "deep_rl_policy.py": 15, "safe_learning.py": 15, "online_learning.py": 15,
    "meta_learning_signal_trust.py": 15, "aiem_v3_learning.py": 15,
    "aiem_module2_decay.py": 15, "aiem_module3_promotion.py": 15,
    "aiem_module4_gate.py": 15,
    # Phase 16
    "alerts.py": 16, "email_alerts.py": 16, "sms_alerts.py": 16,
    "telegram_charts.py": 16, "news_catalyst.py": 16, "news_catalyst_monitor.py": 16,
    "reddit_sentiment.py": 16, "social_sentiment.py": 16, "earnings_calendar.py": 16,
    # Phase 17
    "strict_aeim_supervisor_verifier.py": 17, "strict_observability_supervisor_verifier.py": 17,
    "verify_aiem_loop.py": 17, "verify_eod_learning_loop.py": 17,
    "verify_ml_infrastructure.py": 17, "verify_premarket_system.py": 17,
    "verify_signals.py": 17, "aiem_verification.py": 17, "aiem_v3_verification.py": 17,
    "monitor.py": 17, "drift_alarm.py": 17, "fix_silent_excepts.py": 17,
}

# Modules named in more than one phase in the spec text, and why the
# canonical phase above was chosen. Not silently resolved -- surfaced here.
OWNERSHIP_NOTES = {
    "daily_loss_limit.py": "EXPLICIT CORRECTION from spec: canonical=Phase 11 (Risk Gate & Position Sizing). Also referenced as a dependency in Phase 2 (Guardrails) spec text.",
    "execution.py": "EXPLICIT CORRECTION from spec: canonical=Phase 13 (Execution & Shadow Trading). Also referenced as a dependency in Phase 12 (Edge Filter & Exit Engine) spec text.",
    "execution_simulator.py": "EXPLICIT CORRECTION from spec: canonical=Phase 13 (Execution & Shadow Trading). Also referenced as a dependency in Phase 12 (Edge Filter & Exit Engine) spec text.",
    "backtest.py": "EXPLICIT CORRECTION from spec: canonical=Phase 7 (Statistical Validation & Backtesting). All backtest_*.py siblings also referenced as a dependency group in Phase 14 (Performance Audit) spec text.",
    "simulation_lock.py": "AUTO-RESOLVED (first-appearance rule, NOT an explicit spec correction): named in both Phase 2 and Phase 13 spec text. Assigned to Phase 2 as first occurrence. Flagged for confirmation.",
    "shadow_ledger.py": "AUTO-RESOLVED (first-appearance rule, NOT an explicit spec correction): named in both Phase 2 and Phase 13 spec text. Assigned to Phase 2 as first occurrence. Flagged for confirmation.",
    "evaluation_windows.py": "AUTO-RESOLVED (first-appearance rule): named in both Phase 9 and Phase 14 (dependency) spec text. Assigned to Phase 9.",
    "historical_performance.py": "AUTO-RESOLVED (first-appearance rule): named in both Phase 7 and Phase 14 (dependency) spec text. Assigned to Phase 7.",
    "adversarial_critique.py": "AUTO-RESOLVED (first-appearance rule): named in both Phase 4 and Phase 10 spec text. Assigned to Phase 4.",
    "aiem_module5_discovery.py": "AUTO-RESOLVED (first-appearance rule): named in both Phase 4 and Phase 15 spec text. Assigned to Phase 4.",
    "aiem_module6_rediscovery.py": "AUTO-RESOLVED (first-appearance rule): named in both Phase 4 and Phase 15 spec text. Assigned to Phase 4.",
    "aiem_pipeline_audit.py": "AUTO-RESOLVED (first-appearance rule): named in both Phase 14 and Phase 17 spec text. Assigned to Phase 14.",
}
for _bt in [
    "backtest_deeper.py", "backtest_options.py", "backtest_results.py", "backtest_harness.py",
    "backtest_tiers.py", "backtest_week.py", "backtest_eod_swing.py", "backtest_falsenegatives.py",
    "backtest_losers.py", "backtest_morning_losers.py", "backtest_grinder.py",
    "backtest_grinder_losers.py", "backtest_combo60.py", "backtest_highfactor.py",
    "backtest_quant_vs_v2.py",
]:
    OWNERSHIP_NOTES[_bt] = OWNERSHIP_NOTES["backtest.py"]

# Tools that exist ONLY as standalone owner-run CLI scripts, never wired
# into the AI-callable tool map.
CLI_VERIFICATION_TOOLS = {
    "verify_aiem_loop": "python3 verify_aiem_loop.py",
    "verify_eod_learning_loop": "python3 verify_eod_learning_loop.py",
    "verify_ml_infrastructure": "python3 verify_ml_infrastructure.py",
    "verify_premarket_system": "python3 verify_premarket_system.py",
    "verify_signals": "python3 verify_signals.py",
    "drift_alarm": "python3 drift_alarm.py",
}

# Spec tool-names that are NOT registered under that literal name, but map
# to real, already-registered tool(s).
TOOL_ALIASES = {
    "rl_position_sizer": {
        "real_tools": ["rl_get_paper_action", "rl_status", "rl_strategy_weights",
                        "rl_readable_policy", "rl_ppo_policy", "rl_counterfactuals"],
        "note": "rl_position_sizer.py is the owning MODULE (Phase 11/15), not a tool name. "
                "Its functionality is exposed under the listed real tool names.",
    },
    "portfolio_correlation_risk": {
        "real_tools": ["portfolio_circuit_breaker_status"],
        "note": "portfolio_correlation_risk is an internal check used inside the risk-gate "
                "flow (main.py ~line 39693), not a standalone AI-callable tool today. "
                "Closest registered proxy tool listed; exact coverage still PENDING_VERIFICATION.",
    },
}

PHASE_TOOLS = {
    0: ["get_daily_candidates", "query_independent_picks", "compare_independent_vs_website_picks",
        "mkt_screen_by_indicator", "mkt_screen_period", "mkt_segment_by_cap_tier",
        "mkt_segment_by_sector", "mkt_refresh_universe"],
    1: ["v2_run_cycle", "v2_status", "run_level2", "run_level3", "get_live_snapshot",
        "get_decisions", "log_decision", "log_prediction"],
    2: ["fetch_historical_prices_pit", "mkt_check_survivorship", "check_signal_data_availability",
        "simulation_lock_check", "check_kill_switch", "clear_kill_switch_halt",
        "kill_switch_events", "correlation_guard_status", "liquidity_filter_status",
        "portfolio_circuit_breaker_status", "portfolio_circuit_breaker_reset"],
    3: ["get_current_regime", "get_regime_flags", "query_market_regime", "regime_overlay_check",
        "regime_overlay_manual", "run_regime_filtered_backtest", "momentum_macro_regime",
        "mkt_regime_filter", "mkt_term_structure", "mkt_cta_triggers", "econ_is_high_impact_day",
        "event_risk_check", "event_risk_filter_status"],
    4: ["discovery_run_cycle", "discovery_status", "discovery_list_candidates",
        "discovery_get_candidate", "discovery_promote_candidate", "discovery_reject_candidate",
        "discover_numeric_patterns", "breakout_discover", "breakout_extract_features",
        "causal_discover", "mkt_generate_hypotheses", "mkt_load_discoveries", "mkt_save_discovery",
        "mkt_discover_interactions", "mkt_explore_dimensions", "mkt_find_behavioral_matches",
        "mkt_find_historical_analogs", "mkt_behavioral_templates", "mkt_invent_indicator",
        "search_past_findings", "send_discovery_alert", "register_hypothesis",
        "register_hypotheses", "rank_hypothesis_candidates", "list_hypotheses",
        "list_signal_dimensions"],
    5: ["mkt_compute_indicators", "mkt_candlestick_patterns", "mkt_chart_patterns",
        "mkt_price_patterns", "mkt_price_structure", "mkt_volume_patterns",
        "mkt_52week_momentum", "mkt_compute_momentum", "momentum_trade_score",
        "momentum_optimize_filters", "gap_continuation_score", "intraday_compute_features",
        "intraday_continuation_score", "vwap_compute_features", "vwap_price_vs",
        "vwap_reclaim_detect", "squeeze_subscore", "test_stock_panic_exhaustion",
        "run_panic_exhaustion_backtest", "mkt_capitulation_detector",
        "mkt_extreme_move_reversion", "mkt_accumulation_squeeze", "mkt_quiet_accumulation",
        "mkt_pre_squeeze_warning", "divergence_scan", "check_price_bullish"],
    6: ["mkt_options_flow_scan", "mkt_options_predicts_price", "mkt_options_skew",
        "mkt_ticker_options_history", "mkt_cross_confirm_options", "mkt_gex_scan",
        "option_b_evaluate", "option_b_status", "microstructure_proxy",
        "smart_money_divergence", "mkt_net_flow_db"],
    7: ["stat_arb_check", "run_statistical_significance", "run_granger_test", "run_backtest",
        "run_aiem_self_backtest", "run_gspc_full_history_backtest",
        "run_vix_spike_reversal_grid", "mkt_layer9_score", "mkt_required_pvalue",
        "mkt_validate_oos", "mkt_retrospective_backtest", "mkt_test_signal",
        "mkt_test_inverse", "mkt_factor_correlations", "multivariate_regression",
        "ml_classification_metrics", "ml_regression_metrics", "benchmark_vs_baselines",
        "analyze_metrics", "review_own_accuracy", "walk_forward_validate"],
    8: ["build_features", "ml_train_model", "ml_time_split", "ml_estimate_fill",
        "ml_gp_signal_search", "model_version_history", "save_research_model",
        "evaluate_previous_model", "rollback_to_previous_model", "retrain_pending",
        "retrain_approve", "retrain_reject", "retrain_history",
        "get_meta_learning_weights", "get_m2_decay_status", "get_m6_rediscovery_status"],
    9: ["alpha_score_ticker", "predict_short_term", "strategy_ensemble",
        "ensemble_combine_signals", "mkt_build_composite", "mkt_compare_signals",
        "mkt_check_redundancy", "mkt_analyze_false_signals", "mkt_analyze_top_movers",
        "analyze_signal_correlation", "signal_magnitude_analysis",
        "query_cross_signal_overlap", "query_rank_effectiveness", "query_temporal_patterns",
        "query_missed_movers", "query_pick_outcomes", "query_own_prediction_performance",
        "decision_quality_summary", "record_decision_outcome", "record_human_eval_decision"],
    10: ["adversarial_review", "strategy_ensemble"],
    11: ["run_risk_gate", "check_portfolio_concentration", "portfolio_allocate",
         "portfolio_correlation_risk", "portfolio_circuit_breaker_status",
         "kelly_position_size", "rl_position_sizer", "estimate_options_slippage",
         "execution_realistic_cost", "liquidity_filter_status", "check_daily_loss_limit",
         "correlation_guard_status"],
    12: ["edge_filter_evaluate", "edge_filter_status", "run_risk_gate", "rl_get_paper_action",
         "deep_rl_get_paper_action", "query_exit_timing", "holding_period_optimize",
         "get_decisions", "log_decision"],
    13: ["open_shadow_trade", "close_shadow_trade", "start_shadow_window", "start_eval_window",
         "close_eval_window", "is_eval_window_active", "safe_learning_log_trade",
         "simulation_audit_trail", "shadow_stats", "execution_realistic_cost",
         "record_decision_outcome"],
    14: ["analyze_independent_performance", "analyze_missed_movers", "compare_picks_vs_misses",
         "query_pick_outcomes", "query_own_prediction_performance", "decision_quality_summary",
         "eval_window_history", "review_own_accuracy", "safe_learning_stats", "shadow_stats",
         "signal_layer_redundancy"],
    15: ["adaptive_layer_evaluate", "adaptive_layer_history", "safe_learning_update",
         "safe_learning_weights", "safe_learning_stats", "trust_apply_to_candidates",
         "trust_classify_context", "trust_get_history", "trust_get_weights", "trust_update",
         "rl_counterfactuals", "rl_get_paper_action", "deep_rl_probe",
         "deep_rl_get_paper_action", "rl_ppo_policy", "rl_readable_policy", "rl_status",
         "rl_strategy_weights", "check_shadow_promotion", "test_new_signal",
         "test_scoring_hypothesis", "gate_history", "get_bh_fdr_status", "retrain_pending",
         "retrain_approve", "retrain_reject", "rollback_to_previous_model"],
    16: ["send_discovery_alert", "get_literature_briefs", "reddit_sentiment",
         "check_news_catalyst_risk", "event_risk_check"],
    17: ["verify_aiem_loop", "verify_eod_learning_loop", "verify_ml_infrastructure",
         "verify_premarket_system", "verify_signals", "simulation_audit_trail",
         "decision_quality_summary", "model_version_history", "drift_alarm",
         "run_statistical_significance"],
}

# Tools deliberately excluded from AI's autonomous call list, per main.py
# _TOOL_REGISTRY_INTENTIONAL_EXCLUSIONS (line ~35523). Kept EXACTLY as-is.
EXCLUDED_SAFETY_TOOLS = {
    "clear_kill_switch_halt": "clears a safety halt",
    "close_shadow_trade": "modifies shadow-trade state",
    "log_decision": "writes to decision audit trail",
    "open_shadow_trade": "opens paper position",
    "record_decision_outcome": "writes to audit trail",
    "record_human_eval_decision": "writes to audit trail",
    "retrain_approve": "promotes a model to live",
    "retrain_reject": "rejects a model retrain",
    "run_risk_gate": "can trigger send_discovery_alert",
    "send_discovery_alert": "fires email/SMS to subscribers",
}
