# AIEM DASHBOARD — PHASE A
## Database Inventory
**Generated:** 2026-07-21 | **DB:** PostgreSQL via DATABASE_URL | **Total tables:** 580

---

## Classification Key
- **ACTIVE_AND_POPULATED** — non-zero rows, confirmed written by runtime code, recently updated
- **ACTIVE_BUT_EMPTY** — code writes to it but 0 rows currently
- **PARTIALLY_WIRED** — some code paths write, sparse or stale data
- **LEGACY** — backup table, no active writes
- **UNUSED** — exists in schema, no active code writes found
- **UNKNOWN** — cannot determine without deeper code scan

---

## Tier 1: Core Operational Tables (ACTIVE_AND_POPULATED)

| Table | Rows | Earliest | Latest | PK | Key Columns | Dashboard Screen |
|-------|------|----------|--------|----|----|------|
| polygon_market_daily | 3,346,234 | (varies) | 2026-07-20 | — | ticker, close, volume, gap_pct | All data-driven screens |
| td_intraday_cache | 145,669 | — | 2026-07-21 | — | ticker, timestamp, close, volume | Command Center |
| ticker_market_cap_cache | 13,094 | — | — | — | ticker, market_cap, float_shares | Opportunity Queue |
| unusual_calls_log | 8,212 | — | — | id | ticker, scan_date, voi, premium | Options Intelligence |
| telegram_alert_ledger | 1,144 | 2026-07-13 | 2026-07-21 | id | ticker, alert_type, sent_at | System Operations |
| cta_trigger_scan | 3,500 | 2026-07-13 | 2026-07-21 | id | ticker, scan_date, score | Opportunity Queue |
| behavioral_pattern_matches | 1,286 | — | — | id | ticker, scan_time, pattern_type | Indicator Laboratory |
| oe_indicator_snapshots | 1,739 | — | — | id | ticker, canonical_id, raw_value, normalized_value, confidence | Indicator Laboratory |
| aiem_pipeline_audit_log | 284 | 2026-07-11 | 2026-07-21 | id | trace_id, ticker, logged_at, stage | Audit & Verification |
| signal_fire_log | 283 | — | — | id | signal_name, ticker, fire_date, metadata | Research & Hypotheses |
| aiem_verification_log | 358 | — | — | id | — | Audit & Verification |
| oe_decision_audit | 341 | 2026-07-19 | 2026-07-21 | decision_id (text) | parent_id, input_hash, output_hash, verification_status, identity_json, technical_json, options_intel_json, is_test_record | Decision Proof |
| d3_governance_decisions | 94 | — | — | id | governance_decision_id, trace_id, checkpoint, decision | Audit & Verification |
| aiem_specialist_council_runs | 219 | 2026-07-12 | 2026-07-21 | id | run_time, context, ticker, trace_id, registered_members (jsonb) | Specialist Council |
| aiem_supervisor_event_log | 194 | 2026-07-11 | 2026-07-21 | id | created_at, event_type, detail | System Operations |
| aiem_supervisor_loop_audit | 88 | 2026-07-14 | 2026-07-21 | id | audit_trace_id, trade_id, ticker | Audit & Verification |
| candlestick_confluence_signals | 269 | — | — | id | ticker, pattern_type | Indicator Laboratory |
| options_structure_scan | 406 | — | — | id | ticker, direction, score | Options Intelligence |
| opening_snapshots | 116 | — | — | id | ticker, snapshot_time | Command Center |
| aiem_position_sizing_log | 207 | 2026-07-12 | 2026-07-21 | id | logged_at, ticker, signal_source, conviction_score, entry_price | Portfolio Risk |
| regime_history | 42 | 2026-07-11 | 2026-07-21 | id | recorded_at, series_id, raw_value, vote, regime_label | Command Center |
| signal_outcomes | 95 | — | — | id | signal_name, ticker, outcome | Research & Hypotheses |
| aiem_research_audit_sessions | 93 | — | — | id | — | Research & Hypotheses |
| polygon_rvol_scan | 110 | 2026-07-10 | 2026-07-20 | id | scan_date, ticker, rvol | Opportunity Queue |
| oe_scheduler_trace | 67 | — | — | id | — | System Operations |
| oe_legacy_replay_exceptions | 305 | — | — | id | decision_id, status, field | Audit & Verification |
| aiem_probability_engine_daily_picks | 10 | 2026-07-16 | 2026-07-21 | id | created_at, pick_date, rank, ticker, score, prob_up_1d/2d/3d/4d, confidence | Probability & Calibration |
| aiem_probability_engine_live_queries | 46 | — | — | id | — | Probability & Calibration |
| aiem_probability_engine_predictions | 10 | — | — | id | — | Probability & Calibration |
| aiem_process_predictions | 60 | 2026-07-14 | 2026-07-21 | id | prediction_date, ticker, rank, confidence_score, signal_basis, reasoning | Opportunity Queue |
| aiem_paper_trades | 31 | 2026-07-12 | 2026-07-21 | id | trade_date, ticker, trade_type, entry_price, quantity | Paper Trading |
| aiem_paper_execution_log | 20 | — | — | id | — | Paper Trading |
| options_pipeline_jobs | 20 | 2026-07-16 | 2026-07-21 | id | ticker, scan_date, status, claim_id, trace_id, direction, selected_score | Live Decisions |
| paper_trade_job_ledger | 5 | 2026-07-15 | 2026-07-21 | id | business_date, status, trigger_source, claimed_at, picks_count | System Operations |
| paper_trade_watchdog_heartbeat | 2,321 | 2026-07-15 | 2026-07-21 | id | process_type, execution_id, last_alive, status | System Operations |
| daily_pipeline_runs | 6 | — | — | id | — | System Operations |
| oe_strategy_registry | 42 | — | — | id | strategy_id, name, family, direction, call_put_type, enabled | Live Decisions |
| aiem_signal_discoveries | 5 | — | — | id | hypothesis_text, conditions_json, horizon, signal_n, signal_win_rate | Research & Hypotheses |
| bull_bear_debates | 11 | — | — | id | — | Specialist Council |
| oe_gate_events | 4 | 2026-07-21 | 2026-07-21 | gate_event_id (text) | gate_name, fired_at, ticker, trace_id, live_hash, expected_hash, action_taken, is_test_record | Audit & Verification |
| d3_governance_event_links | 183 | — | — | id | governance_decision_id, execution_plan_id, paper_trade_id | Audit & Verification |
| d3_governance_requests | 9 | — | — | id | — | Audit & Verification |
| d3_governance_acks | 9 | — | — | id | — | Audit & Verification |
| d3_change_log | 32 | — | — | id | — | Audit & Verification |
| d3_governance_components | 6 | — | — | id | — | Audit & Verification |

---

## Tier 2: Active But Sparse / Partially Wired (PARTIALLY_WIRED)

| Table | Rows | Notes |
|-------|------|-------|
| aiem_portfolio_state | 0 | Schema exists, portfolio_engine writes it but no live trades |
| ape_portfolio_snapshots | 0 | Phase 4 portfolio engine, no active positions |
| ape_portfolio_greeks | 0 | Greeks only computed when positions exist |
| ape_gate_decisions | 0 | Phase 4 gate, never triggered |
| ape_stress_results | 0 | Phase 4 stress test, no positions |
| risk_gate_decisions | 0 | pre_decision_risk_gate.py writes — fires when trades exist |
| aiem_research_hypotheses | 0 | hypothesis_registry.py — no approved hypotheses registered |
| aiem_risk_scores | 0 | risk score table, schema ready |
| order_execution_log | 0 | order_dedup.py writes — no live orders |
| oe_trade_records | 0 | Phase 3 trade recording — no closed options trades |
| oe_pattern_snapshots | 19 | Options pattern snapshots |
| oe_options_metrics | 14 | Options metrics, partially populated |

---

## Tier 3: Active But Empty (ACTIVE_BUT_EMPTY)

Tables where code verifiably writes but currently 0 rows (market conditions, weekend, or not yet triggered):

| Table | Writer Module | Reason Empty |
|-------|--------------|--------------|
| aiem_ml_retrain_log | ml_engine.py | Retrain runs Sunday 8PM only |
| aiem_predictions | prediction_logger.py | ML predictions path not active |
| sc_morning_picks | main.py | Small-cap picks only fire on qualifying days |
| options_prob_deep_itm_daily | aiem_optprob.py | Deep ITM filter — rarely triggers |
| aiem_paper_thompson | main.py | Thompson sampling — no active Thompson positions |
| rl_strategy_weights | aiem_rl_engine.py | RL not in active paper trading path |
| regime_flags | regime_detector.py | Only written on flag events |

---

## Tier 4: Legacy Tables (LEGACY)

| Table | Notes |
|-------|-------|
| aiem_paper_trades_backup_20260709 | 0 rows, backup only |
| aiem_paper_thompson_backup_20260709 | 0 rows, backup only |
| aiem_paper_thompson_history_backup_20260709 | 0 rows, backup only |
| aiem_paper_execution_log_backup_20260709 | 0 rows, backup only |
| signal_trust_history_backup_20260709 | 0 rows, backup only |
| signal_trust_weights_backup_20260709 | 0 rows, backup only |

---

## Tier 5: Large Reference Tables (READ-HEAVY, ACTIVE)

| Table | Rows | Purpose |
|-------|------|---------|
| polygon_market_daily | 3,346,234 | EOD OHLCV for all US stocks |
| polygon_indicators_daily | 3,261,314 | Precomputed daily indicators |
| td_intraday_cache | 145,669 | Tradier intraday bars |
| ticker_market_cap_cache | 13,094 | Market cap + float reference |
| ticker_lifecycle | ~3M (from pg_total bytes) | Ticker status history |
| ticker_market_cap_cache | 13,094 | Cap/float cache |

---

## Key Column Details for Dashboard Screens

### aiem_paper_trades (31 rows)
`id, trade_date, ticker, trade_type, entry_price, quantity, exit_price, exit_date, status, signal_source, conviction_score, stop_price, target_price, created_at, d2_trace_id, position_size_usd, slippage_pct, fill_price, notes`

### oe_decision_audit (341 rows)
`decision_id (TEXT PK), parent_id, created_at, input_hash, output_hash, verification_status, engine_version, db_version, is_test_record, identity_json (JSONB), technical_json (JSONB), options_intel_json (JSONB), probability_json (JSONB), council_json (JSONB), risk_json (JSONB)`
> **Note:** is_test_record=TRUE rows exist from testing; production reads require WHERE is_test_record=FALSE

### options_pipeline_jobs (20 rows)
`id, ticker, scan_date, status, claim_id, trace_id, alert_id, direction, selected_score, trigger_source, error_text, recovery_attempts, created_at, completed_at, heartbeat_at`

### oe_gate_events (4 rows)
`gate_event_id (TEXT PK), gate_name, fired_at, ticker, trace_id, live_hash, expected_hash, mismatch_detail, decision_context (JSONB), action_taken, is_test_record, authenticated_by`

---

## Tables With No Dashboard Value (UNUSED/EXCLUDED)
~200 tables exist for backtesting, RL experimentation, shadow ledgers, and dev scaffolding. Not listed individually — confirmed by 0 rows and no active runtime writer. Examples:
- All `rl_*` tables (12 tables) — reinforcement learning, not in production path
- All `shadow_*` tables — shadow position tracking, 0 rows
- `spy_daily_cache`, `vix_daily` — 0 rows, replaced by polygon_market_daily
- `answers`, `questions` — NCLEX tables, different product
