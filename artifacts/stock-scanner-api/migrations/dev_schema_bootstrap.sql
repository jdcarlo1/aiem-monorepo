-- dev_schema_bootstrap.sql
-- PURPOSE: Ensures dev DB has the same tables as prod before any deployment.
-- Run this after any dev DB reset / first-time setup:
--   psql $DATABASE_URL -f artifacts/stock-scanner-api/migrations/dev_schema_bootstrap.sql
--
-- All statements are idempotent (CREATE TABLE IF NOT EXISTS).
-- This script exists because the codebase uses inline CREATE TABLE IF NOT EXISTS
-- inside Python functions (lazy schema creation), not a tracked ORM migration
-- system. Tables only appear in a database after the code path that creates them
-- has been executed. Dev doesn't run all production code paths, so tables drift.
-- Running this script syncs dev to prod schema before every deploy.
--
-- Last updated: 2026-07-11
-- Tables covered: all 60 tables that were prod-only as of 2026-07-11 audit.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Originally created in prod by aiem_morning_brief / prediction_logger ─────
CREATE TABLE IF NOT EXISTS aiem_predictions (
    id SERIAL PRIMARY KEY,
    prediction_date DATE NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    rank INTEGER,
    confidence_score NUMERIC(5,2),
    signal_basis TEXT,
    reasoning TEXT,
    predicted_move TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(prediction_date, ticker)
);

CREATE TABLE IF NOT EXISTS aiem_prediction_outcomes (
    id SERIAL PRIMARY KEY,
    prediction_date DATE NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    t1_return NUMERIC(8,4),
    t3_return NUMERIC(8,4),
    t5_return NUMERIC(8,4),
    win_t3 BOOLEAN,
    win_t5 BOOLEAN,
    graded_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(prediction_date, ticker)
);

-- ── Originally created in prod by _aiem_save_independent_picks ───────────────
CREATE TABLE IF NOT EXISTS aiem_independent_picks (
    id SERIAL PRIMARY KEY,
    pick_date DATE NOT NULL,
    pick_type VARCHAR(20) NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    rank INTEGER,
    confidence_score NUMERIC(5,2),
    rationale TEXT,
    features JSONB,
    entry_price NUMERIC(12,4),
    option_strike NUMERIC(10,2),
    option_expiry DATE,
    hold_days_max INTEGER DEFAULT 5,
    status VARCHAR(20) DEFAULT 'open',
    exit_price NUMERIC(12,4),
    exit_date DATE,
    pnl_pct NUMERIC(8,4),
    direction_correct BOOLEAN,
    pnl_methodology VARCHAR(40) DEFAULT 'underlying_close_pct',
    source VARCHAR(40) DEFAULT 'aiem_independent_polygon',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Originally created in prod by prediction_logger.py ───────────────────────
CREATE TABLE IF NOT EXISTS aiem_ml_predictions (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    trade_date DATE NOT NULL,
    predicted_prob FLOAT,
    features_json JSONB,
    outcome INTEGER,
    return_pct FLOAT,
    model_version TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (ticker, trade_date)
);

-- ── Originally created in prod by retrain_pipeline.py ────────────────────────
CREATE TABLE IF NOT EXISTS aiem_ml_retrain_log (
    id SERIAL PRIMARY KEY,
    retrain_date DATE NOT NULL,
    n_samples INTEGER,
    candidate_auc FLOAT,
    candidate_brier FLOAT,
    prod_auc FLOAT,
    prod_brier FLOAT,
    promoted BOOLEAN,
    reason TEXT,
    metrics_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Originally created in prod by _aiem_tool_register_hypotheses ─────────────
CREATE TABLE IF NOT EXISTS aiem_research_hypotheses (
    id SERIAL PRIMARY KEY,
    research_date DATE NOT NULL,
    hypothesis_index INTEGER NOT NULL,
    hypothesis_text TEXT NOT NULL,
    registered_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(research_date, hypothesis_index)
);

-- ── Originally created in prod by _history_link_ensure_table ─────────────────
CREATE TABLE IF NOT EXISTS aiem_history_tokens (
    token TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- ── Originally created in prod by aiem_probability_engine/live_query.py ──────
CREATE TABLE IF NOT EXISTS aiem_probability_engine_live_queries (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    mode TEXT NOT NULL,
    model_version TEXT,
    pit_status TEXT NOT NULL,
    request_json JSONB,
    envelope_json JSONB,
    verified BOOLEAN,
    verify_reason TEXT
);

-- ── Originally created in prod by aiem_probability_engine/daily_picks.py ─────
CREATE TABLE IF NOT EXISTS aiem_probability_engine_daily_picks (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    pick_date DATE NOT NULL,
    rank SMALLINT NOT NULL,
    ticker TEXT NOT NULL,
    model_version TEXT NOT NULL,
    score DOUBLE PRECISION,
    prob_up_1d DOUBLE PRECISION,
    prob_up_2d DOUBLE PRECISION,
    prob_up_3d DOUBLE PRECISION,
    prob_up_4d DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    edge_after_cost_prob_pts DOUBLE PRECISION,
    regime_tag TEXT,
    top_contributing_layers_json JSONB,
    warnings_json JSONB,
    UNIQUE (pick_date, ticker)
);

-- ── aiem_probability_engine tables ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_probability_engine_predictions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    model_version TEXT NOT NULL,
    probability_source_json JSONB,
    prob_up_1d DOUBLE PRECISION, prob_up_2d DOUBLE PRECISION,
    prob_up_3d DOUBLE PRECISION, prob_up_4d DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    top_contributing_layers_json JSONB, overlays_json JSONB,
    warnings_json JSONB, feature_snapshot_json JSONB,
    outcome_ret_1d DOUBLE PRECISION, outcome_ret_2d DOUBLE PRECISION,
    outcome_ret_3d DOUBLE PRECISION, outcome_ret_4d DOUBLE PRECISION,
    outcome_label_1d SMALLINT, outcome_label_2d SMALLINT,
    outcome_label_3d SMALLINT, outcome_label_4d SMALLINT,
    outcome_last_checked_at TIMESTAMPTZ,
    regime_tag TEXT, edge_after_cost_prob_pts DOUBLE PRECISION, pit_status TEXT
);

CREATE TABLE IF NOT EXISTS aiem_probability_engine_pit_corrections (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    original_prediction_id BIGINT NOT NULL,
    signal_date DATE NOT NULL, ticker TEXT NOT NULL, correction_status TEXT NOT NULL,
    corrected_prob_up_1d DOUBLE PRECISION, corrected_prob_up_2d DOUBLE PRECISION,
    corrected_prob_up_3d DOUBLE PRECISION, corrected_prob_up_4d DOUBLE PRECISION,
    training_cutoff_1d DATE, training_cutoff_2d DATE,
    training_cutoff_3d DATE, training_cutoff_4d DATE,
    n_training_samples_1d INTEGER, n_training_samples_2d INTEGER,
    n_training_samples_3d INTEGER, n_training_samples_4d INTEGER,
    n_training_dates_1d INTEGER, n_training_dates_2d INTEGER,
    n_training_dates_3d INTEGER, n_training_dates_4d INTEGER
);

-- ── AIEM Process (isolated scanner) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_process_predictions (
    id SERIAL PRIMARY KEY,
    prediction_date DATE NOT NULL, ticker VARCHAR(10) NOT NULL,
    rank INTEGER, confidence_score NUMERIC(5,1),
    signal_basis TEXT, reasoning TEXT, predicted_move TEXT,
    created_at TIMESTAMPTZ DEFAULT now(), gap_pct DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS aiem_process_outcomes (
    id SERIAL PRIMARY KEY,
    prediction_date DATE NOT NULL, ticker VARCHAR(10) NOT NULL,
    entry_price NUMERIC(10,4), t1_price NUMERIC(10,4), t1_return NUMERIC(8,4),
    t3_price NUMERIC(10,4), t3_return NUMERIC(8,4), win_t3 BOOLEAN,
    t5_price NUMERIC(10,4), t5_return NUMERIC(8,4), win_t5 BOOLEAN,
    graded_at TIMESTAMPTZ
);

-- ── AIEM Bus / Decision / Scan ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_bus_transfer_log (
    id BIGSERIAL PRIMARY KEY, trace_id TEXT NOT NULL, ticker TEXT,
    stage_order INTEGER, stage_name TEXT, event_type TEXT, component_name TEXT,
    payload JSONB, published_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS aiem_decision_log (
    id BIGSERIAL PRIMARY KEY, ticker TEXT, trade_date DATE,
    decision_type TEXT, decision_rationale TEXT, signal_source TEXT,
    confidence_score NUMERIC, final_decision TEXT,
    audit_trace_id TEXT, created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aiem_scan_log (
    id SERIAL PRIMARY KEY, scan_type VARCHAR(50),
    tickers_found INTEGER, tickers_passed INTEGER, scan_dt TIMESTAMP DEFAULT now()
);

-- ── AIEM Registry tables (function / module / tool) ───────────────────────────
CREATE TABLE IF NOT EXISTS aiem_function_registry (
    function_row_id SERIAL PRIMARY KEY, file_name TEXT NOT NULL, function_name TEXT NOT NULL,
    purpose TEXT, inputs TEXT, outputs TEXT,
    upstream_dependencies TEXT, downstream_dependencies TEXT,
    owning_phase INTEGER, owning_phase_name TEXT, owning_module TEXT,
    is_inline BOOLEAN NOT NULL DEFAULT true,
    verification_status TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    verification_evidence TEXT, verified_by_command TEXT,
    last_verified_date TIMESTAMPTZ, verification_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aiem_module_registry (
    module_id SERIAL PRIMARY KEY, module_name TEXT NOT NULL, module_file TEXT NOT NULL,
    module_phase INTEGER NOT NULL, module_phase_name TEXT,
    owned_tools TEXT[], required_inputs TEXT, produced_outputs TEXT,
    upstream_modules TEXT[], downstream_modules TEXT[],
    verification_required BOOLEAN NOT NULL DEFAULT true, audit_log_enabled BOOLEAN,
    execution_status TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    last_verified_date TIMESTAMPTZ, verified_by_command TEXT,
    verification_result TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    verification_version INTEGER NOT NULL DEFAULT 0,
    ownership_note TEXT, file_exists_confirmed BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ownership_status TEXT NOT NULL DEFAULT 'CONFIRMED'
);

CREATE TABLE IF NOT EXISTS aiem_tool_registry (
    tool_id SERIAL PRIMARY KEY, tool_name TEXT NOT NULL,
    owning_module_or_phase TEXT, tool_type TEXT NOT NULL DEFAULT 'ai_callable_tool',
    required_inputs TEXT, produced_outputs TEXT,
    can_run_independently BOOLEAN, requires_market_data BOOLEAN,
    requires_options_data BOOLEAN, requires_historical_data BOOLEAN,
    requires_trade_history BOOLEAN, writes_audit_log BOOLEAN,
    excluded_from_autonomous_use BOOLEAN NOT NULL DEFAULT false,
    exclusion_reason TEXT, alias_of TEXT, dependency_notes TEXT,
    registered_in_tool_map BOOLEAN NOT NULL DEFAULT false,
    verification_status TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    last_verified_date TIMESTAMPTZ, verified_by_command TEXT,
    verification_result TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
    verification_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    owning_module TEXT, tool_verification_level TEXT NOT NULL DEFAULT 'phase_only'
);

-- ── AIEM Supervisor tables ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_supervisor_bad_learning_flags (
    id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    audit_trace_id TEXT, trade_id BIGINT, ticker TEXT, signal_source TEXT,
    flag_type TEXT NOT NULL, old_value NUMERIC, new_value NUMERIC,
    expected_allowed_change NUMERIC, sample_size INTEGER,
    reason TEXT, supervisor_action TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aiem_supervisor_overfit_checks (
    id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_id INTEGER, signal_source TEXT, audit_trace_id TEXT,
    overfit_score NUMERIC NOT NULL DEFAULT 0, sample_size INTEGER, filter_count INTEGER,
    in_sample_edge NUMERIC, out_of_sample_edge NUMERIC, recent_edge NUMERIC,
    regime_stability_score NUMERIC, outlier_dependency_score NUMERIC,
    verdict TEXT NOT NULL, action TEXT
);

CREATE TABLE IF NOT EXISTS aiem_supervisor_overrides (
    id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    audit_trace_id TEXT, ticker TEXT, trade_id BIGINT,
    aiem_original_decision TEXT, aiem_original_confidence NUMERIC,
    supervisor_final_decision TEXT, supervisor_adjusted_confidence NUMERIC,
    override_type TEXT, reason TEXT, evidence_json JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS aiem_supervisor_performance_reports (
    id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_type TEXT NOT NULL, period_start DATE NOT NULL, period_end DATE NOT NULL,
    total_alerts INTEGER NOT NULL DEFAULT 0, total_trades INTEGER NOT NULL DEFAULT 0,
    win_rate NUMERIC, avg_pnl_pct NUMERIC, max_drawdown NUMERIC,
    confidence_calibration_score NUMERIC, learning_quality_score NUMERIC,
    risk_discipline_score NUMERIC, overall_supervisor_grade TEXT,
    report_json JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS aiem_supervisor_risk_checks (
    id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    audit_trace_id TEXT, ticker TEXT, trade_id BIGINT,
    risk_score NUMERIC NOT NULL DEFAULT 0, risk_flags_json JSONB NOT NULL DEFAULT '[]',
    approved_by_aiem BOOLEAN NOT NULL DEFAULT true,
    approved_by_supervisor BOOLEAN NOT NULL DEFAULT true,
    supervisor_action TEXT NOT NULL, reason TEXT
);

CREATE TABLE IF NOT EXISTS aiem_supervisor_signal_health (
    signal_source TEXT, total_trades BIGINT, wins BIGINT,
    win_rate_pct NUMERIC, avg_pnl_pct NUMERIC, lifecycle_status TEXT
);

CREATE TABLE IF NOT EXISTS aiem_supervisor_signal_lifecycle (
    id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_id INTEGER, signal_name TEXT NOT NULL, signal_source TEXT NOT NULL,
    current_status TEXT NOT NULL, new_status TEXT NOT NULL, reason TEXT,
    sample_size INTEGER, win_rate NUMERIC, avg_return NUMERIC,
    recent_return NUMERIC, regime_stability NUMERIC,
    oos_status TEXT, supervisor_decision TEXT
);

-- ── AIEM Verification / Track-record / Attribution ───────────────────────────
CREATE TABLE IF NOT EXISTS aiem_verification_log (
    id BIGSERIAL PRIMARY KEY, job_id TEXT NOT NULL, unix_timestamp TEXT,
    openai_response_id TEXT, verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    client_ip TEXT, verified BOOLEAN DEFAULT true, failure_reason TEXT, job_type TEXT
);

CREATE TABLE IF NOT EXISTS aiem_verify_link_tokens (
    token TEXT NOT NULL, job_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS aiem_track_record (
    id SERIAL PRIMARY KEY, session_id TEXT, created_at TIMESTAMPTZ DEFAULT now(),
    ticker TEXT NOT NULL, direction TEXT NOT NULL, horizon_days INTEGER NOT NULL DEFAULT 3,
    confidence TEXT NOT NULL, predicted_win_pct NUMERIC(5,1), rationale TEXT,
    entry_price NUMERIC(12,4), target_date DATE, outcome_price NUMERIC(12,4),
    outcome_pct NUMERIC(8,2), was_correct BOOLEAN, graded_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS aiem_trade_attribution (
    id BIGSERIAL PRIMARY KEY, trade_id BIGINT, ticker TEXT NOT NULL,
    signal_source TEXT NOT NULL, entry_price NUMERIC(12,4), exit_price NUMERIC(12,4),
    pnl_pct NUMERIC(10,6), win BOOLEAN, hold_days INTEGER,
    module_credits JSONB, blame_vector JSONB, confidence_at_entry NUMERIC(6,4),
    attribution_version TEXT DEFAULT 'v1', trace_id TEXT,
    attributed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── AIEM Watch / Alerts ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_watch_criteria (
    id BIGSERIAL PRIMARY KEY, discovered_date DATE NOT NULL, expires_at DATE NOT NULL,
    origin_ticker TEXT NOT NULL, origin_bucket TEXT, origin_move_pct NUMERIC,
    reason_cat TEXT NOT NULL, metric_name TEXT NOT NULL, operator TEXT NOT NULL,
    threshold_value NUMERIC NOT NULL, observed_value NUMERIC, lookback_days INTEGER DEFAULT 1,
    source_text TEXT, validation_n INTEGER, validation_win_rate NUMERIC,
    validation_avg_next_day NUMERIC, active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aiem_watch_alerts (
    id BIGSERIAL PRIMARY KEY, criteria_id BIGINT NOT NULL,
    ticker TEXT NOT NULL, alert_date DATE NOT NULL, job_name TEXT,
    observed_value NUMERIC, sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── AIEM Misc / Caches ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_finding_embeddings (
    research_date DATE NOT NULL, findings_text TEXT,
    embedding JSONB, created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aiem_ticker_reference_cache (
    ticker TEXT NOT NULL, market_cap NUMERIC, float_shares NUMERIC,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aiem_signals (
    id SERIAL PRIMARY KEY, ticker VARCHAR(20) NOT NULL, scan_dt TIMESTAMP,
    action VARCHAR(20), base_conviction REAL, final_conviction REAL,
    gap_pct REAL, price REAL, vwap REAL, market_cap BIGINT,
    catalyst_source VARCHAR(40), catalyst_age_h REAL, tags TEXT, notes TEXT,
    signal_date DATE, entry_price REAL, close_price REAL,
    outcome VARCHAR(20), pct_move REAL, created_at TIMESTAMP DEFAULT now()
);

-- ── Health log (orphaned from removed code, but data exists in prod) ──────────
CREATE TABLE IF NOT EXISTS aiem_health_log (
    id SERIAL PRIMARY KEY, status VARCHAR(20), detail TEXT,
    checked_at TIMESTAMP DEFAULT now()
);

-- ── Backup snapshots from 2026-07-09 ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_paper_execution_log_backup_20260709 (
    id INTEGER, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
    status TEXT, trades_inserted INTEGER, error_msg TEXT
);
CREATE TABLE IF NOT EXISTS aiem_paper_thompson_backup_20260709 (
    id BIGINT, signal_source TEXT, alpha NUMERIC(10,4), beta NUMERIC(10,4),
    wins INTEGER, losses INTEGER, sampled_score NUMERIC(10,4),
    last_updated TIMESTAMPTZ, last_audit_trace_id TEXT, last_trade_id TEXT, last_ticker TEXT
);
CREATE TABLE IF NOT EXISTS aiem_paper_thompson_history_backup_20260709 (
    id BIGINT, recorded_at TIMESTAMPTZ, signal_source TEXT,
    old_alpha NUMERIC(10,4), old_beta NUMERIC(10,4),
    new_alpha NUMERIC(10,4), new_beta NUMERIC(10,4),
    win_loss TEXT, reward NUMERIC(10,4), pnl_pct NUMERIC(10,4),
    ticker TEXT, trade_id TEXT, audit_trace_id TEXT
);
CREATE TABLE IF NOT EXISTS aiem_paper_trades_backup_20260709 (
    id INTEGER, trade_date DATE, ticker TEXT, trade_type TEXT,
    entry_price NUMERIC(14,4), quantity NUMERIC(14,4), notional NUMERIC(12,2),
    signal_source TEXT, signal_detail TEXT, hold_days_max INTEGER, status TEXT,
    exit_price NUMERIC(14,4), exit_date DATE, pnl NUMERIC(12,2), pnl_pct NUMERIC(10,4),
    last_price NUMERIC(14,4), created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
    exit_reason TEXT, needs_review BOOLEAN, review_reason TEXT,
    strike NUMERIC(10,2), expiry TEXT, mid_price NUMERIC, fill_price NUMERIC,
    spread_pct_used NUMERIC, unachievable_fill BOOLEAN, illiquid_fill BOOLEAN,
    pre_sizing_model BOOLEAN, sizing_stop_price NUMERIC(14,4), sizing_stop_basis TEXT,
    sizing_risk_pct NUMERIC(8,4), sizing_log_id BIGINT, sizing_gate_result TEXT,
    audit_trace_id TEXT, entry_score NUMERIC(14,6),
    thompson_multiplier_applied NUMERIC(8,4), thompson_sampled_score NUMERIC(8,4),
    thompson_signal_source TEXT
);
CREATE TABLE IF NOT EXISTS signal_trust_history_backup_20260709 (
    id INTEGER, signal_name TEXT, context_bucket TEXT, trust_weight NUMERIC,
    rolling_win_rate NUMERIC, recorded_at TIMESTAMPTZ, audit_trace_id TEXT,
    trade_id TEXT, ticker TEXT, old_trust_score NUMERIC(10,6), new_trust_score NUMERIC(10,6),
    delta NUMERIC(10,6), reason_for_change TEXT, win_loss_result TEXT,
    pnl NUMERIC(14,4), pnl_pct NUMERIC(10,4), n_trades_used INTEGER, learning_module_source TEXT
);
CREATE TABLE IF NOT EXISTS signal_trust_weights_backup_20260709 (
    id INTEGER, signal_name TEXT, context_bucket TEXT, rolling_win_rate NUMERIC,
    n_outcomes_observed INTEGER, trust_weight NUMERIC, last_updated_at TIMESTAMPTZ
);

-- ── Market data caches ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gspc_daily (
    id SERIAL PRIMARY KEY, scan_date DATE NOT NULL, close_price DOUBLE PRECISION NOT NULL,
    open_price DOUBLE PRECISION, high_price DOUBLE PRECISION, low_price DOUBLE PRECISION,
    volume BIGINT, has_intraday_data BOOLEAN
);
CREATE TABLE IF NOT EXISTS vix_daily (
    scan_date DATE NOT NULL, vix_close DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS spy_daily_cache (
    date DATE NOT NULL, open DOUBLE PRECISION, high DOUBLE PRECISION,
    low DOUBLE PRECISION, close DOUBLE PRECISION, volume BIGINT,
    spy_daily_ret DOUBLE PRECISION, vix_close DOUBLE PRECISION
);

-- ── Backtest result tables ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS panic_exhaustion_backtest_runs (
    id BIGSERIAL PRIMARY KEY, run_time TIMESTAMPTZ DEFAULT now(),
    period_label TEXT, start_date DATE, end_date DATE,
    spy_threshold_pct DOUBLE PRECISION, hold_days INTEGER, stop_loss_pct DOUBLE PRECISION,
    data_available BOOLEAN, earliest_spy_in_range DATE, latest_spy_in_range DATE,
    n INTEGER, win_rate DOUBLE PRECISION, avg_return_pct DOUBLE PRECISION,
    worst_trade_pct DOUBLE PRECISION, num_stop_outs INTEGER,
    max_consecutive_losses INTEGER, cumulative_return_pct DOUBLE PRECISION, trades_json TEXT
);
CREATE TABLE IF NOT EXISTS stock_panic_exhaustion_results (
    id BIGSERIAL PRIMARY KEY, run_time TIMESTAMPTZ DEFAULT now(),
    period_label TEXT, threshold_pct DOUBLE PRECISION, hold_days INTEGER,
    stop_loss_pct DOUBLE PRECISION, min_price DOUBLE PRECISION,
    start_date DATE, end_date DATE, require_spy_panic BOOLEAN,
    spy_panic_threshold DOUBLE PRECISION, n INTEGER,
    wr_hold_with_stop DOUBLE PRECISION, wr_hold_no_stop DOUBLE PRECISION,
    wr_5d DOUBLE PRECISION, wr_20d DOUBLE PRECISION, avg_ret_hold DOUBLE PRECISION,
    worst_trade DOUBLE PRECISION, best_trade DOUBLE PRECISION, num_stop_outs INTEGER,
    avg_signal_depth_20d DOUBLE PRECISION, distinct_tickers INTEGER
);
CREATE TABLE IF NOT EXISTS gp_discovered_templates (
    id BIGSERIAL PRIMARY KEY, evolved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    formula TEXT NOT NULL, fitness NUMERIC(10,6), complexity INTEGER,
    training_n INTEGER, status TEXT NOT NULL DEFAULT 'pending_review'
);

-- ── Operational logs ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_log (
    id BIGSERIAL PRIMARY KEY, job_name TEXT NOT NULL, ran_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS drift_check_log (
    id SERIAL PRIMARY KEY, checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_source TEXT NOT NULL, live_wr NUMERIC, live_trades INTEGER,
    bt_wr NUMERIC, bt_trades INTEGER, gap_pp NUMERIC, verdict TEXT,
    telegram_sent BOOLEAN NOT NULL DEFAULT false, telegram_error TEXT
);
CREATE TABLE IF NOT EXISTS dc_template_feedback (
    id BIGSERIAL PRIMARY KEY, discovery_id INTEGER NOT NULL,
    category TEXT NOT NULL, verdict TEXT NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ask_email_processed_uids (
    uid TEXT NOT NULL, question TEXT, confirmation_sent BOOLEAN DEFAULT true,
    answer_sent BOOLEAN DEFAULT false, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS signing_key_events (
    id SERIAL PRIMARY KEY, event_type TEXT NOT NULL, old_key_hash TEXT,
    new_key_hash TEXT NOT NULL, rotated_by TEXT NOT NULL,
    rotated_at TIMESTAMPTZ NOT NULL DEFAULT now(), notes TEXT
);
CREATE TABLE IF NOT EXISTS reddit_sentiment_log (
    id SERIAL PRIMARY KEY, ticker VARCHAR(10), sentiment_score DOUBLE PRECISION,
    post_count INTEGER, checked_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS slippage_estimates (
    id SERIAL PRIMARY KEY, ticker VARCHAR(10),
    estimated_slippage_pct DOUBLE PRECISION,
    estimated_cost_per_contract DOUBLE PRECISION, checked_at TIMESTAMPTZ NOT NULL
);

-- ── Core data / EOD ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS earnings_calendar (
    id SERIAL PRIMARY KEY, ticker VARCHAR(10) NOT NULL,
    earnings_date DATE NOT NULL, timing VARCHAR(20) DEFAULT 'unknown'
);
CREATE TABLE IF NOT EXISTS eod_outcomes (
    id SERIAL PRIMARY KEY, trade_date DATE NOT NULL, ticker TEXT NOT NULL,
    open_price NUMERIC(10,2), close_price NUMERIC(10,2),
    high_price NUMERIC(10,2), low_price NUMERIC(10,2),
    open_to_close_pct NUMERIC(8,2), open_to_high_pct NUMERIC(8,2),
    fade_risk_signal TEXT, standout_score NUMERIC(12,2),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS opening_snapshots (
    id SERIAL PRIMARY KEY, ticker VARCHAR(10) NOT NULL,
    scan_time TIMESTAMPTZ NOT NULL, price DOUBLE PRECISION NOT NULL,
    volume BIGINT, scan_date DATE NOT NULL DEFAULT CURRENT_DATE
);
CREATE TABLE IF NOT EXISTS scan_history (
    id SERIAL PRIMARY KEY, scan_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    scan_date DATE NOT NULL, ticker TEXT NOT NULL,
    price NUMERIC(10,2), prev_close NUMERIC(10,2), price_chg_pct NUMERIC(8,2),
    gap_pct NUMERIC(8,2), momentum_open NUMERIC(8,2), exhaustion_ratio NUMERIC(6,3),
    fade_risk TEXT, rel_vol NUMERIC(8,1), today_vol BIGINT, avg_vol BIGINT,
    inflow_m NUMERIC(12,2), outflow_m NUMERIC(12,2), net_m NUMERIC(12,2),
    flow_ratio NUMERIC(8,2), standout_score NUMERIC(12,2),
    mkt_cap_m NUMERIC(14,1), rank_in_scan INTEGER
);
CREATE TABLE IF NOT EXISTS ticker_lifecycle (
    ticker VARCHAR(10) NOT NULL, active BOOLEAN,
    listed_date DATE, delisted_date DATE, updated_at TIMESTAMPTZ DEFAULT now()
);

-- aiem_candidate_queue: full candidate evaluation log (build items 1+3, phase 4)
CREATE TABLE IF NOT EXISTS aiem_candidate_queue (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    trade_date DATE NOT NULL DEFAULT CURRENT_DATE,
    ticker TEXT NOT NULL,
    source TEXT,
    detail TEXT,
    trade_type TEXT,
    direction TEXT,
    raw_score NUMERIC,
    drift_mult NUMERIC,
    trust_mult NUMERIC,
    thompson_mult NUMERIC,
    thompson_sampled_score NUMERIC,
    final_score NUMERIC,
    raw_probability NUMERIC,
    calibrated_probability NUMERIC,
    ev NUMERIC,
    composite_score NUMERIC,
    risk_gate_result TEXT,
    risk_gate_reason TEXT,
    execution_cost_est NUMERIC,
    no_trade_reason TEXT,
    rejection_reason TEXT,
    rejecting_stage TEXT,
    final_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    audit_trace_id TEXT,
    is_test_record BOOLEAN NOT NULL DEFAULT FALSE,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS aiem_candidate_queue_trade_date_idx ON aiem_candidate_queue(trade_date);
CREATE INDEX IF NOT EXISTS aiem_candidate_queue_ticker_idx ON aiem_candidate_queue(ticker);
CREATE INDEX IF NOT EXISTS aiem_candidate_queue_status_idx ON aiem_candidate_queue(final_status);
CREATE INDEX IF NOT EXISTS aiem_candidate_queue_run_id_idx ON aiem_candidate_queue(run_id);
