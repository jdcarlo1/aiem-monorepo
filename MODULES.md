# StockScanner AI — MODULES.md

> Auto-generated inventory of all modules, tools, endpoints, and database schema.


---
## 1. Backend Python Modules

| File | Purpose |
|---|---|
| `advanced_quant_indicators.py` | ADVANCED QUANT INDICATORS LIBRARY |
| `adversarial_critique.py` | adversarial_critique.py |
| `alerts.py` | — |
| `analytics.py` | Historical analytics engine. |
| `automated_retrain_pipeline.py` | automated_retrain_pipeline.py |
| `backtest.py` | — |
| `backtest_combo60.py` | backtest_combo60.py — Systematic multi-factor combination search for 60%+ WR |
| `backtest_deeper.py` | backtest_deeper.py — Find NEW indicators in the filtered-out losers |
| `backtest_double_signal.py` | Backtest double-signal for a specific date. |
| `backtest_eod_swing.py` | EOD Swing Backtest — Jun 1–5 + Jun 9–13, 2026 (10 trading days) |
| `backtest_etf_gate.py` | ETF Gate Comparison: Open-of-Day vs VWAP — Jun 1–5 + Jun 9–13, 2026 |
| `backtest_falsenegatives.py` | backtest_falsenegatives.py — What did the filtered-out WINNERS have in common? |
| `backtest_grinder.py` | Steady Grinder backtest — Jun 9–13, 2026. |
| `backtest_grinder_losers.py` | Grinder Loser Autopsy — Jun 1–5 + Jun 9–13, 2026 |
| `backtest_highfactor.py` | backtest_highfactor.py — 4 through 7-factor combination search |
| `backtest_losers.py` | backtest_losers.py — Find what the losing signals have in common |
| `backtest_morning_losers.py` | Morning Burst Loser Autopsy — Jun 1–5 + Jun 9–13, 2026 |
| `backtest_morning_vs_grinder.py` | Side-by-side backtest: Morning Scanner vs Steady Grinder — Jun 1-5 + Jun 9-13, 2026 |
| `backtest_newrates.py` | backtest_newrates.py — Project win rates under new combined filter logic |
| `backtest_options.py` | Options backtest: AI short-term calls that expired June 12, 2026. |
| `backtest_quant_vs_v2.py` | backtest_quant_vs_v2.py |
| `backtest_rescued.py` | backtest_rescued.py — Can we rescue winners from the filtered buckets? |
| `backtest_results.py` | Post-signal performance for the 5 grinder hits found last week. |
| `backtest_tiers.py` | backtest_tiers.py  —  Multi-Day Runner threshold backtest |
| `backtest_week.py` | Steady Grinder full-week backtest: Jun 1–5, 2026. |
| `benchmark_comparison.py` | benchmark_comparison.py |
| `breakout_signature_discovery.py` | breakout_signature_discovery.py |
| `causal_discovery.py` | causal_discovery.py |
| `causal_inference.py` | causal_inference.py |
| `composite_scan.py` | Manual / scheduled Composite Score scan across the full optionable universe |
| `congress_trades.py` | Congress trades fetcher. |
| `data_prep.py` | data_prep.py |
| `decision_logger.py` | decision_logger.py |
| `deep_rl_policy.py` | deep_rl_policy.py |
| `email_alerts.py` | Email subscription manager + daily digest sender. |
| `ensemble_combiner.py` | ensemble_combiner.py |
| `eod_swing.py` | EOD Swing Setup Scanner — runs at 2:00 PM ET Mon-Fri. |
| `evaluation_metrics.py` | evaluation_metrics.py |
| `evaluation_windows.py` | evaluation_windows.py |
| `execution.py` | — |
| `execution_simulator.py` | execution_simulator.py |
| `factors.py` | — |
| `feature_engineering.py` | feature_engineering.py |
| `historical_performance.py` | Historical performance tracking for Smart Money scores. |
| `holding_period_optimizer.py` | holding_period_optimizer.py |
| `holy_grail.py` | Holy Grail Signals — 9 advanced intraday indicators computed from 1-minute data. |
| `hypothesis_registry.py` | hypothesis_registry.py |
| `indicators.py` | — |
| `insider_trades.py` | C-Suite Insider Trades via yfinance insider_transactions. |
| `intraday_continuation_scanner.py` | intraday_continuation_scanner.py |
| `kill_switch.py` | kill_switch.py |
| `layer9_statistical_edge.py` | LAYER 9: STATISTICAL EDGE |
| `literature_scanner.py` | literature_scanner.py |
| `main.py` | ── DB connect timeout global default ──────────────────────────────────────── |
| `market_regime_overlay.py` | market_regime_overlay.py |
| `meta_learning_signal_trust.py` | meta_learning_signal_trust.py |
| `ml_engine.py` | — |
| `model_training.py` | model_training.py |
| `monitor.py` | Uptime monitor — completely standalone, zero impact on main.py or any tab. |
| `multiday_runner.py` | multiday_runner.py — Multi-Day Runner Scanner (All Cap Tiers) |
| `news_catalyst.py` | News Catalyst Scanner — completely parallel track alongside the ICS momentum scanner. |
| `niche_segment_finder.py` | niche_segment_finder.py |
| `online_learning.py` | online_learning.py |
| `options_sweep.py` | Call Sweep Scanner — Institutional Conviction Score (ICS) Engine |
| `pnl.py` | — |
| `portfolio.py` | — |
| `portfolio_allocator.py` | portfolio_allocator.py |
| `position_sizing.py` | position_sizing.py |
| `pre_decision_risk_gate.py` | pre_decision_risk_gate.py |
| `prediction_logger.py` | prediction_logger.py |
| `premarket_gap_continuation_scanner.py` | premarket_gap_continuation_scanner.py |
| `prop_signal.py` | — |
| `regime.py` | — |
| `regime_monitor.py` | regime_monitor.py |
| `retrain_pipeline.py` | retrain_pipeline.py |
| `rl_position_sizer.py` | rl_position_sizer.py |
| `scanner.py` | — |
| `scoring.py` | — |
| `self_coding_orchestrator.py` | self_coding_orchestrator.py |
| `shadow_ledger.py` | shadow_ledger.py |
| `signal_correlation.py` | signal_correlation.py |
| `signal_discovery_gp.py` | signal_discovery_gp.py |
| `signal_magnitude_analysis.py` | signal_magnitude_analysis.py |
| `signal_outcomes.py` | Signal Outcome Tracker |
| `simulation_lock.py` | simulation_lock.py |
| `smart_money.py` | ── Mega-cap tech ───────────────────────────────────────────────────────── |
| `smart_money_divergence_detector.py` | smart_money_divergence_detector.py |
| `sms_alerts.py` | Real-time stock alerts delivered by EMAIL. |
| `vwap_indicators.py` | vwap_indicators.py |

---
## 2. AIEM Research Tools (88 tools)

These are the tools the AIEM agent uses when answering questions.

| Tool Name | Description |
|---|---|
| `mkt_52week_momentum` | AIEM research tool |
| `mkt_accumulation_squeeze` | AIEM research tool |
| `mkt_analyze_false_signals` | AIEM research tool |
| `mkt_analyze_top_movers` | Analyze top-% movers on a given date: what they had in common beforehand |
| `mkt_behavioral_templates` | Load pre-move behavioral fingerprint templates from 2,946 historical patterns |
| `mkt_build_composite` | AIEM research tool |
| `mkt_cap_b` | AIEM research tool |
| `mkt_cap_m` | AIEM research tool |
| `mkt_capitulation_detector` | AIEM research tool |
| `mkt_check_redundancy` | AIEM research tool |
| `mkt_check_survivorship` | AIEM research tool |
| `mkt_compare_signals` | AIEM research tool |
| `mkt_compute_indicators` | Compute full technical indicator set (SMA/EMA/RSI/MACD/ADX/BB/Keltner/OBV etc.) |
| `mkt_compute_momentum` | AIEM research tool |
| `mkt_cross_confirm_options` | Cross-confirm price signals with options flow confirmation |
| `mkt_discover_interactions` | AIEM research tool |
| `mkt_explore_dimensions` | Explore which signal dimensions (factors) correlate with future returns |
| `mkt_extreme_move_reversion` | AIEM research tool |
| `mkt_factor_correlations` | Compute pairwise correlations between scoring factors |
| `mkt_find_behavioral_matches` | Find stocks whose current 14-dim fingerprint matches pre-move templates (similarity score) |
| `mkt_find_thresholds` | Find optimal signal thresholds (e.g. what RVOL cutoff maximizes edge) |
| `mkt_gap_fill_probability` | AIEM research tool |
| `mkt_generate_hypotheses` | AIEM research tool |
| `mkt_get_stock_history` | AIEM research tool |
| `mkt_historical_study` | AIEM research tool |
| `mkt_invent_indicator` | AIEM research tool |
| `mkt_layer9_score` | Run full 9-layer statistical edge score on a ticker |
| `mkt_load_discoveries` | Load all previously discovered signals |
| `mkt_net_flow_db` | Query net equity flow (buy vol minus sell vol) from database |
| `mkt_options_flow_scan` | Scan full universe for unusual options flow meeting criteria |
| `mkt_options_predicts_price` | Test whether options flow preceded price moves (lead-lag analysis) |
| `mkt_pre_squeeze_warning` | Detect pre-squeeze conditions: high SI + low float + rising OI |
| `mkt_price_patterns` | AIEM research tool |
| `mkt_quiet_accumulation` | Detect quiet accumulation: rising volume + tight price range (no attention yet) |
| `mkt_refresh_universe` | AIEM research tool |
| `mkt_regime_filter` | AIEM research tool |
| `mkt_required_pvalue` | AIEM research tool |
| `mkt_retrospective_backtest` | Backtest any signal on historical data with configurable horizon |
| `mkt_save_discovery` | Save a validated signal discovery to aiem_signal_discoveries table |
| `mkt_screen_by_indicator` | Screen all 11K+ tickers by any of 38 indicators |
| `mkt_screen_period` | AIEM research tool |
| `mkt_segment_by_cap_tier` | AIEM research tool |
| `mkt_segment_by_sector` | AIEM research tool |
| `mkt_signal_drift` | AIEM research tool |
| `mkt_test_inverse` | AIEM research tool |
| `mkt_test_signal` | Test a new signal hypothesis against historical data, return win rate + p-value |
| `mkt_ticker_deep_compare` | Compare two tickers side-by-side across all scoring layers |
| `mkt_ticker_options_history` | Pull historical options sweep data for a specific ticker |
| `mkt_validate_oos` | Validate a signal out-of-sample (holdout period test) |
| `mkt_volatility_squeeze` | AIEM research tool |
| `mkt_volume_patterns` | AIEM research tool |

---
## 3. API Endpoints

Total: 175 routes

| Endpoint | Notes |
|---|---|
| `/stock-api` | |
| `/stock-api/` | |
| `/stock-api/52week-breakout` | |
| `/stock-api/admin/accumulation/breakouts` | |
| `/stock-api/admin/accumulation/exit-check` | |
| `/stock-api/admin/accumulation/positions` | |
| `/stock-api/admin/backfill-iv` | |
| `/stock-api/admin/grade-short-calls` | |
| `/stock-api/admin/job-health` | |
| `/stock-api/admin/job-heartbeats` | |
| `/stock-api/admin/model/current` | |
| `/stock-api/admin/model/history` | |
| `/stock-api/admin/model/train` | |
| `/stock-api/admin/news-catchup` | |
| `/stock-api/admin/owner-catchup` | |
| `/stock-api/admin/pre-squeeze/validate` | |
| `/stock-api/admin/predictable-events/52week-momentum` | |
| `/stock-api/admin/predictable-events/capitulation` | |
| `/stock-api/admin/predictable-events/capitulation/validate` | |
| `/stock-api/admin/predictable-events/dividends/<ticker>` | |
| `/stock-api/admin/predictable-events/extreme-move` | |
| `/stock-api/admin/predictable-events/gap-fill` | |
| `/stock-api/admin/predictable-events/lockup-expirations` | |
| `/stock-api/admin/reset-breaker` | |
| `/stock-api/admin/run-ai-stock-picks` | |
| `/stock-api/admin/run-aiem-continuous-research` | |
| `/stock-api/admin/run-aiem-grader` | |
| `/stock-api/admin/run-aiem-morning-scan` | |
| `/stock-api/admin/run-aiem-research` | |
| `/stock-api/admin/run-eod-scan` | |
| `/stock-api/admin/run-historical-backfill` | |
| `/stock-api/admin/run-polygon-rvol` | |
| `/stock-api/admin/seed-conviction-data` | |
| `/stock-api/admin/send-market-brief` | |
| `/stock-api/admin/signal-bridge/grade` | |
| `/stock-api/admin/signal-bridge/performance` | |
| `/stock-api/admin/test-emails` | |
| `/stock-api/ai-analyze` | |
| `/stock-api/ai-early-movers` | |
| `/stock-api/ai-short-calls` | |
| `/stock-api/ai-short-calls-log` | |
| `/stock-api/ai-stock-picks` | |
| `/stock-api/ai-trade-log` | |
| `/stock-api/ai-trades` | |
| `/stock-api/ai-trades/backfill-flow` | |
| `/stock-api/ai-trades/regenerate` | |
| `/stock-api/ai/thesis` | |
| `/stock-api/aiem-predictions` | |
| `/stock-api/aiem-research-status` | |
| `/stock-api/aiem/chat` | |
| `/stock-api/aiem/chat/<job_id>` | |
| `/stock-api/aiem/chat/history` | |
| `/stock-api/aiem/discoveries` | |
| `/stock-api/alerts` | |
| `/stock-api/alerts/<int:alert_id>` | |
| `/stock-api/alerts/count` | |
| `/stock-api/alerts/subscribe` | |
| `/stock-api/alerts/test-digest` | |
| `/stock-api/alerts/unsubscribe/<token>` | |
| `/stock-api/analytics/historical` | |
| `/stock-api/backtest` | |
| `/stock-api/behavioral-matches` | |
| `/stock-api/breakout/radar` | |
| `/stock-api/bull-flow/history` | |
| `/stock-api/bull-flow/persistence` | |
| `/stock-api/bull-flow/top10` | |
| `/stock-api/charm-cascade` | |
| `/stock-api/check-subscription` | |
| `/stock-api/composite-leaderboard` | |
| `/stock-api/composite-outcomes/trigger` | |
| `/stock-api/composite-scan/status` | |
| `/stock-api/composite-scan/trigger` | |
| `/stock-api/composite-score` | |
| `/stock-api/composite-snapshot/status` | |
| `/stock-api/composite-snapshot/trigger` | |
| `/stock-api/composite-track-record` | |
| `/stock-api/congress/trades` | |
| `/stock-api/convergence` | |
| `/stock-api/conviction-calls` | |
| `/stock-api/conviction-history` | |
| `/stock-api/conviction-outcomes` | |
| `/stock-api/conviction-stack` | |
| `/stock-api/conviction-stack-outcomes/trigger` | |
| `/stock-api/conviction-stack-snapshot/trigger` | |
| `/stock-api/conviction-stack-track-record` | |
| `/stock-api/conviction-stack/score/<ticker>` | |
| `/stock-api/cross-scanner` | |
| `/stock-api/daily-top10` | |
| `/stock-api/darkpool` | |
| `/stock-api/earnings-calendar` | |
| `/stock-api/eod-accum-track` | |
| `/stock-api/eod-accumulation` | |
| `/stock-api/eod-sweep-track-record` | |
| `/stock-api/eod-sweeps` | |
| `/stock-api/etf-calls` | |
| `/stock-api/far-otm-sweeps` | |
| `/stock-api/float-pressure` | |
| `/stock-api/flow-streak/latest` | |
| `/stock-api/full-market-movers` | |
| `/stock-api/gamma-pressure` | |
| `/stock-api/gamma-pressure/trigger` | |
| `/stock-api/gamma-wall` | |
| `/stock-api/gap-volume-signal` | |
| `/stock-api/grinder-scan` | |
| `/stock-api/healthz` | |
| `/stock-api/ics-thesis` | |
| `/stock-api/insider-alerts` | |
| `/stock-api/insider-outcomes` | |
| `/stock-api/insider-radar` | |
| `/stock-api/insider/trades` | |
| `/stock-api/iv-rank` | |
| `/stock-api/iv-rank/scan` | |
| `/stock-api/market-press` | |
| `/stock-api/market/overview` | |
| `/stock-api/morning-inflows` | |
| `/stock-api/morning-runners` | |
| `/stock-api/multi-signal` | |
| `/stock-api/multi-signal/ai-thesis` | |
| `/stock-api/multi-signal/log` | |
| `/stock-api/multiday-runners` | |
| `/stock-api/my-trades` | |
| `/stock-api/my-trades/<int:trade_id>` | |
| `/stock-api/nano-morning/candidates` | |
| `/stock-api/nano-morning/grade` | |
| `/stock-api/nano-morning/picks` | |
| `/stock-api/nano-morning/run-ranking` | |
| `/stock-api/nano-morning/send-buy` | |
| `/stock-api/nano-morning/send-watch` | |
| `/stock-api/nano-quant/latest` | |
| `/stock-api/nano-watchlist` | |
| `/stock-api/net-flow` | |
| `/stock-api/net-flow/ai-signal` | |
| `/stock-api/net-flow/microcap` | |
| `/stock-api/net-flow/microcap/tickers` | |
| `/stock-api/net-flow/multiday` | |
| `/stock-api/net-flow/single` | |
| `/stock-api/oi-accumulation` | |
| `/stock-api/oi-snapshot/trigger` | |
| `/stock-api/outcomes` | |
| `/stock-api/portfolio` | |
| `/stock-api/portfolio/buy` | |
| `/stock-api/portfolio/sell` | |
| `/stock-api/premarket` | |
| `/stock-api/prop/reset` | |
| `/stock-api/prop/scan` | |
| `/stock-api/prop/trade/<ticker>/<action>` | |
| `/stock-api/runner-outcomes` | |
| `/stock-api/sc-morning/candidates` | |
| `/stock-api/sc-morning/grade` | |
| `/stock-api/sc-morning/picks` | |
| `/stock-api/sc-morning/run-ranking` | |
| `/stock-api/sc-morning/send-buy` | |
| `/stock-api/sc-morning/send-watch` | |
| `/stock-api/sector-heat` | |
| `/stock-api/sector-rotation` | |
| `/stock-api/short-squeeze` | |
| `/stock-api/smart-money/cache-status` | |
| `/stock-api/smart-money/detail/<ticker>` | |
| `/stock-api/smart-money/scan` | |
| `/stock-api/sms/incoming` | |
| `/stock-api/squeeze-setup` | |
| `/stock-api/squeeze-setup/ai-signal` | |
| `/stock-api/squeeze/detector` | |
| `/stock-api/standout-track` | |
| `/stock-api/stock/analyze` | |
| `/stock-api/stock/scan` | |
| `/stock-api/stock/watchlist` | |
| `/stock-api/trade-watchlist` | |
| `/stock-api/trade-watchlist/<int:trade_id>` | |
| `/stock-api/unusual-calls` | |
| `/stock-api/unusual-calls-log` | |
| `/stock-api/unusual-calls/microcap` | |
| `/stock-api/unusual-calls/microcap/scan` | |
| `/stock-api/whale-activity` | |
| `/stock-api/whale-history` | |

---
## 4. Quant Agent Chat Endpoints (NEW)

| Endpoint | Method | Description |
|---|---|---|
| `/stock-api/aiem/chat` | POST | Start AIEM research session. Body: `{question: string}`. Returns `{job_id}` immediately. |
| `/stock-api/aiem/chat/<job_id>` | GET | Poll for result. Returns `{status, answer, error}`. Status: pending→running→done/error. |
| `/stock-api/aiem/chat/history` | GET | Last 20 chat sessions from DB. |

Typical response time: **2–4 minutes** (AIEM runs 3 iterations with multiple tool calls each).


---
## 5. Database Schema

Total tables: 97


### `affiliates`
| Column | Type |
|---|---|
| `id` | integer |
| `code` | text |
| `name` | text |
| `stripe_connect_id` | text |
| `commission_pct` | integer |
| `created_at` | timestamp with time zone |

### `agent_decisions`
| Column | Type |
|---|---|
| `id` | integer |
| `decision_time` | timestamp with time zone |
| `signal_name` | text |
| `ticker` | text |
| `decision_type` | text |
| `direction` | text |
| `confidence` | numeric |
| `reasoning` | text |
| `input_state_snapshot` | jsonb |
| `outcome_known` | boolean |
| `outcome_return` | numeric |
| `outcome_recorded_at` | timestamp with time zone |
| `outcome_notes` | text |

### `ai_early_movers_log`
| Column | Type |
|---|---|
| `id` | integer |
| `trade_date` | date |
| `rank` | integer |
| `ticker` | text |
| `rec_type` | text |
| `strike` | double precision |
| `expiry` | text |
| `days_out` | integer |
| `stock_price` | double precision |
| `day_ret` | double precision |
| `confirmed_2d` | boolean |
| `vol_oi` | double precision |
| `prem` | bigint |
| `conviction` | text |
| `thesis` | text |
| `why_it_stands_out` | text |
| `outcome` | text |
| `t3_price` | double precision |
| `t3_pct` | double precision |
| `t3_win` | boolean |
| `t7_price` | double precision |
| `t7_pct` | double precision |
| `t7_win` | boolean |
| `created_at` | timestamp with time zone |

### `ai_early_movers_misses`
| Column | Type |
|---|---|
| `id` | integer |
| `miss_date` | date |
| `ticker` | text |
| `day_ret` | double precision |
| `volume` | bigint |
| `price` | double precision |
| `has_uc` | boolean |
| `created_at` | timestamp with time zone |

### `ai_short_calls_log`
| Column | Type |
|---|---|
| `id` | integer |
| `trade_date` | date |
| `rank` | integer |
| `ticker` | text |
| `strike` | double precision |
| `expiry` | text |
| `days_out` | integer |
| `vol_oi` | double precision |
| `prem` | bigint |
| `stock_price` | double precision |
| `otm_pct` | double precision |
| `breakeven` | double precision |
| `conviction` | text |
| `urgency` | text |
| `thesis` | text |
| `why_it_stands_out` | text |
| `outcome` | text |
| `t1_price` | double precision |
| `t3_price` | double precision |
| `t5_price` | double precision |
| `t1_pct` | double precision |
| `t3_pct` | double precision |
| `t5_pct` | double precision |
| `t1_win` | boolean |
| `t3_win` | boolean |
| `t5_win` | boolean |
| `expiry_price` | double precision |
| `expiry_pct` | double precision |
| `expiry_win` | boolean |
| `created_at` | timestamp with time zone |
| `rec_type` | text |
| `day_ret` | double precision |
| `confirmed_2d` | boolean |

### `ai_short_calls_misses`
| Column | Type |
|---|---|
| `id` | integer |
| `miss_date` | date |
| `ticker` | text |
| `day_ret` | double precision |
| `volume` | bigint |
| `price` | double precision |
| `has_uc` | boolean |
| `created_at` | timestamp with time zone |

### `ai_stock_picks`
| Column | Type |
|---|---|
| `id` | integer |
| `pick_date` | date |
| `ticker` | character varying |
| `score` | double precision |
| `confidence` | character varying |
| `hold_days` | integer |
| `exit_date` | date |
| `target_pct` | double precision |
| `stop_pct` | double precision |
| `entry_note` | text |
| `signals` | jsonb |
| `signal_count` | integer |
| `layer9_score` | double precision |
| `layer9_regime` | character varying |
| `layer9_signal` | text |
| `created_at` | timestamp with time zone |

### `ai_trade_log`
| Column | Type |
|---|---|
| `id` | integer |
| `trade_date` | date |
| `ticker` | text |
| `direction` | text |
| `setup_type` | text |
| `conviction` | text |
| `price_at_signal` | double precision |
| `entry_strike` | double precision |
| `expiry` | text |
| `target_price` | double precision |
| `stop_loss` | double precision |
| `signals_aligned` | jsonb |
| `thesis` | text |
| `risk_level` | text |
| `t1_price` | double precision |
| `t3_price` | double precision |
| `t5_price` | double precision |
| `t10_price` | double precision |
| `t1_pct` | double precision |
| `t3_pct` | double precision |
| `t5_pct` | double precision |
| `t10_pct` | double precision |
| `t1_win` | boolean |
| `t3_win` | boolean |
| `t5_win` | boolean |
| `t10_win` | boolean |
| `outcome` | text |
| `created_at` | timestamp with time zone |
| `expiry_price` | double precision |
| `expiry_pct` | double precision |
| `expiry_win` | boolean |
| `source` | text |
| `option_premium` | double precision |
| `breakeven_price` | double precision |
| `total_premium_usd` | double precision |

### `aiem_ml_predictions`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `trade_date` | date |
| `predicted_prob` | double precision |
| `features_json` | jsonb |
| `outcome` | integer |
| `return_pct` | double precision |
| `model_version` | text |
| `created_at` | timestamp with time zone |
| `resolved_at` | timestamp with time zone |

### `aiem_ml_retrain_log`
| Column | Type |
|---|---|
| `id` | integer |
| `retrain_date` | date |
| `n_samples` | integer |
| `candidate_auc` | double precision |
| `candidate_brier` | double precision |
| `prod_auc` | double precision |
| `prod_brier` | double precision |
| `promoted` | boolean |
| `reason` | text |
| `metrics_json` | jsonb |
| `created_at` | timestamp with time zone |

### `aiem_prediction_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `prediction_date` | date |
| `ticker` | text |
| `entry_price` | double precision |
| `t1_price` | double precision |
| `t1_return` | double precision |
| `t3_price` | double precision |
| `t3_return` | double precision |
| `win_t3` | boolean |
| `t5_price` | double precision |
| `t5_return` | double precision |
| `win_t5` | boolean |
| `graded_at` | timestamp with time zone |

### `aiem_predictions`
| Column | Type |
|---|---|
| `id` | integer |
| `prediction_date` | date |
| `ticker` | text |
| `rank` | integer |
| `confidence_score` | double precision |
| `signal_basis` | text |
| `reasoning` | text |
| `predicted_move` | text |
| `created_at` | timestamp with time zone |

### `aiem_research_insights`
| Column | Type |
|---|---|
| `id` | integer |
| `research_date` | date |
| `findings` | text |
| `scoring_adjustments` | jsonb |
| `confidence` | text |
| `tool_calls_made` | integer |
| `created_at` | timestamp with time zone |

### `aiem_segment_findings`
| Column | Type |
|---|---|
| `id` | integer |
| `search_date` | date |
| `segment_description` | text |
| `n_samples` | integer |
| `win_rate` | double precision |
| `baseline_win_rate` | double precision |
| `lift` | double precision |
| `p_value` | double precision |
| `p_value_adjusted` | double precision |
| `validated_out_of_sample` | boolean |
| `oos_win_rate` | double precision |
| `created_at` | timestamp with time zone |

### `aiem_signal_discoveries`
| Column | Type |
|---|---|
| `id` | integer |
| `hypothesis_text` | text |
| `conditions_json` | jsonb |
| `horizon` | character varying |
| `signal_n` | integer |
| `signal_win_rate` | double precision |
| `signal_avg_ret` | double precision |
| `baseline_n` | integer |
| `baseline_win_rate` | double precision |
| `baseline_avg_ret` | double precision |
| `edge_broad` | double precision |
| `edge_tight` | double precision |
| `p_value` | double precision |
| `oos_edge` | double precision |
| `status` | character varying |
| `discovered_at` | timestamp without time zone |
| `confirmed_at` | timestamp without time zone |
| `invented_indicator` | text |
| `notes` | text |

### `aiem_test_ledger`
| Column | Type |
|---|---|
| `id` | integer |
| `session_date` | date |
| `tool_name` | text |
| `conditions` | jsonb |
| `p_value` | double precision |
| `n` | integer |
| `logged_at` | timestamp without time zone |

### `answers`
| Column | Type |
|---|---|
| `id` | integer |
| `session_id` | text |
| `question_id` | integer |
| `selected_letter` | text |
| `correct` | boolean |
| `created_at` | timestamp with time zone |

### `ask_email_processed_uids`
| Column | Type |
|---|---|
| `uid` | text |
| `question` | text |
| `confirmation_sent` | boolean |
| `answer_sent` | boolean |
| `created_at` | timestamp with time zone |

### `behavioral_pattern_matches`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_time` | timestamp with time zone |
| `ticker` | text |
| `similarity` | numeric |
| `matched_ticker` | text |
| `matched_date` | date |
| `matched_move` | numeric |
| `days_before_move` | integer |
| `current_fingerprint` | jsonb |
| `template_fingerprint` | jsonb |
| `verdict` | text |

### `buyback_announcements`
| Column | Type |
|---|---|
| `ticker` | character varying |
| `announced_date` | date |
| `authorized_amount` | bigint |
| `source_url` | text |

### `call_sweep_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `strike` | numeric |
| `expiry` | text |
| `call_volume` | integer |
| `open_interest` | integer |
| `vol_oi_ratio` | numeric |
| `premium` | integer |
| `stock_price` | numeric |
| `vwap` | numeric |
| `sweep_date` | date |
| `sent_at` | timestamp with time zone |
| `conviction` | integer |
| `signals_fired` | text |

### `composite_score_history`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_date` | date |
| `ticker` | text |
| `score` | numeric |
| `rating` | text |
| `price` | numeric |
| `rsi` | numeric |
| `volume_ratio` | numeric |
| `price_change_pct` | numeric |
| `scanned_at` | timestamp with time zone |

### `composite_watchlist`
| Column | Type |
|---|---|
| `id` | integer |
| `snap_date` | date |
| `ticker` | text |
| `score` | numeric |
| `rating` | text |
| `scan_price` | numeric |
| `volume_ratio` | numeric |
| `rsi` | numeric |
| `price_change_pct` | numeric |
| `entry_date` | date |
| `entry_open` | numeric |
| `entry_price_source` | text |
| `w1_pct` | numeric |
| `w2_pct` | numeric |
| `w3_pct` | numeric |
| `w4_pct` | numeric |
| `captured_at` | timestamp with time zone |
| `updated_at` | timestamp with time zone |

### `conviction_calls_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `snap_date` | date |
| `ticker` | character varying |
| `conviction` | character varying |
| `score` | double precision |
| `entry_price` | double precision |
| `d1_price` | double precision |
| `d1_pct` | double precision |
| `d3_price` | double precision |
| `d3_pct` | double precision |
| `d5_price` | double precision |
| `d5_pct` | double precision |
| `updated_at` | timestamp with time zone |

### `conviction_calls_snapshot`
| Column | Type |
|---|---|
| `id` | integer |
| `snap_date` | date |
| `ticker` | character varying |
| `price` | double precision |
| `score` | double precision |
| `conviction` | character varying |
| `num_strikes` | integer |
| `total_prem_m` | double precision |
| `max_vol_oi` | double precision |
| `avg_iv` | double precision |
| `rank` | integer |
| `saved_at` | timestamp with time zone |

### `conviction_stack_watchlist`
| Column | Type |
|---|---|
| `id` | integer |
| `snap_date` | date |
| `ticker` | character varying |
| `total_pts` | double precision |
| `conviction_pct` | integer |
| `label` | character varying |
| `price` | double precision |
| `layers` | jsonb |
| `meta` | jsonb |
| `rank` | integer |
| `universe_count` | integer |
| `source` | character varying |
| `captured_at` | timestamp with time zone |
| `entry_date` | date |
| `entry_open` | double precision |
| `w1_pct` | double precision |
| `w2_pct` | double precision |
| `w3_pct` | double precision |
| `w4_pct` | double precision |
| `updated_at` | timestamp with time zone |

### `daily_top10`
| Column | Type |
|---|---|
| `scan_date` | date |
| `payload` | jsonb |
| `created_at` | timestamp with time zone |

### `daily_vol_snapshots`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `snap_date` | date |
| `iv_skew` | double precision |
| `short_float` | double precision |
| `pc_oi_ratio` | double precision |
| `pc_prem_ratio` | double precision |
| `rs_vs_spy` | double precision |
| `created_at` | timestamp with time zone |

### `deep_rl_policy_versions`
| Column | Type |
|---|---|
| `id` | integer |
| `policy_name` | text |
| `version` | integer |
| `models_blob` | bytea |
| `feature_names` | jsonb |
| `trained_on_n_samples` | integer |
| `held_out_avg_reward` | numeric |
| `is_live` | boolean |
| `created_at` | timestamp with time zone |
| `probe_report` | jsonb |

### `dividend_calendar`
| Column | Type |
|---|---|
| `ticker` | character varying |
| `ex_date` | date |
| `cash_amount` | double precision |

### `eod_accum_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `pick_date` | date |
| `ticker` | text |
| `entry_price` | numeric |
| `next_open` | numeric |
| `next_open_chg_pct` | numeric |
| `morning_high` | numeric |
| `morning_high_chg_pct` | numeric |
| `gapped_up` | boolean |
| `news_type` | text |
| `accum_score` | numeric |
| `fetched_at` | timestamp with time zone |

### `eod_accum_picks`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_date` | date |
| `ticker` | text |
| `close_price` | numeric |
| `accum_score` | numeric |
| `news_type` | text |
| `news_headline` | text |
| `eod_rel_vol` | numeric |
| `late_flow` | numeric |
| `closing_range` | numeric |
| `price_chg_pct` | numeric |
| `mkt_cap_m` | numeric |
| `scanned_at` | timestamp with time zone |
| `signal_type` | text |

### `eod_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `trade_date` | date |
| `ticker` | text |
| `open_price` | numeric |
| `close_price` | numeric |
| `high_price` | numeric |
| `low_price` | numeric |
| `open_to_close_pct` | numeric |
| `open_to_high_pct` | numeric |
| `fade_risk_signal` | text |
| `standout_score` | numeric |
| `fetched_at` | timestamp with time zone |

### `eod_sweep_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `signal_date` | date |
| `session` | text |
| `score` | numeric |
| `grade` | text |
| `num_strikes` | integer |
| `total_prem_m` | numeric |
| `max_vol_oi` | numeric |
| `avg_iv` | numeric |
| `price_at_signal` | numeric |
| `detected_at` | timestamp with time zone |
| `close_t1` | numeric |
| `close_t3` | numeric |
| `close_t5` | numeric |
| `return_t1` | numeric |
| `return_t3` | numeric |
| `return_t5` | numeric |
| `outcome_updated_at` | timestamp with time zone |

### `evaluation_windows`
| Column | Type |
|---|---|
| `id` | integer |
| `signal_name` | text |
| `window_start` | timestamp with time zone |
| `window_end` | timestamp with time zone |
| `starting_paper_equity` | numeric |
| `status` | text |
| `closed_at` | timestamp with time zone |
| `checkpoint_report` | jsonb |
| `human_decision` | text |
| `human_decision_at` | timestamp with time zone |

### `gamma_pressure_alerts`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | character varying |
| `price` | double precision |
| `price_change_pct` | double precision |
| `fir` | double precision |
| `fsd` | bigint |
| `float_shares` | bigint |
| `float_m` | double precision |
| `call_volume` | integer |
| `avg_delta` | double precision |
| `vol_oi` | double precision |
| `top_strike` | double precision |
| `top_strike_expiry` | character varying |
| `score` | double precision |
| `sms_sent` | boolean |
| `alerted_at` | timestamp with time zone |
| `alert_date` | date |

### `hypothesis_counter`
| Column | Type |
|---|---|
| `id` | integer |
| `total_registered` | integer |

### `hypothesis_registry`
| Column | Type |
|---|---|
| `id` | integer |
| `hypothesis_hash` | text |
| `name` | text |
| `description` | text |
| `parameters` | jsonb |
| `train_start` | date |
| `train_end` | date |
| `test_start` | date |
| `test_end` | date |
| `registered_at` | timestamp with time zone |
| `locked` | boolean |
| `result` | jsonb |
| `result_recorded_at` | timestamp with time zone |

### `index_membership_changes`
| Column | Type |
|---|---|
| `ticker` | character varying |
| `index_name` | character varying |
| `change_type` | character varying |
| `announced_date` | date |
| `effective_date` | date |

### `insider_alerts`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `detected_at` | timestamp with time zone |
| `suspicion_score` | integer |
| `prem` | bigint |
| `strike` | double precision |
| `expiry` | text |
| `price_at_detection` | double precision |
| `vol_oi` | double precision |
| `earnings_date` | date |
| `days_to_earnings` | integer |
| `ticker_appearances` | integer |
| `verdict` | text |
| `pre_positioned` | boolean |
| `outcome_checked` | boolean |

### `insider_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `alert_id` | integer |
| `ticker` | text |
| `earnings_date` | date |
| `price_at_detection` | double precision |
| `price_at_earnings` | double precision |
| `pct_move` | double precision |
| `called_it` | boolean |
| `outcome_verdict` | text |
| `checked_at` | timestamp with time zone |

### `insider_transactions`
| Column | Type |
|---|---|
| `ticker` | character varying |
| `filer_name` | text |
| `transaction_date` | date |
| `transaction_type` | character varying |
| `shares` | bigint |
| `price` | double precision |
| `filed_at` | timestamp without time zone |

### `ipo_calendar`
| Column | Type |
|---|---|
| `ticker` | character varying |
| `ipo_date` | date |
| `assumed_lockup_days` | integer |

### `job_heartbeats`
| Column | Type |
|---|---|
| `job_name` | character varying |
| `last_success` | timestamp without time zone |
| `last_attempt` | timestamp without time zone |
| `last_error` | text |
| `consecutive_failures` | integer |

### `kill_switch_events`
| Column | Type |
|---|---|
| `id` | integer |
| `event_type` | text |
| `reason` | text |
| `metrics_snapshot` | jsonb |
| `created_at` | timestamp with time zone |

### `kill_switch_state`
| Column | Type |
|---|---|
| `id` | integer |
| `halted` | boolean |
| `halted_at` | timestamp with time zone |
| `halted_reason` | text |
| `cleared_at` | timestamp with time zone |
| `cleared_by` | text |

### `literature_briefs`
| Column | Type |
|---|---|
| `id` | integer |
| `query` | text |
| `scanned_at` | timestamp with time zone |
| `sources_json` | jsonb |
| `summary` | text |
| `relevance_to_existing_signals` | text |
| `suggested_next_steps` | text |
| `reviewed` | boolean |
| `reviewed_at` | timestamp with time zone |

### `model_registry`
| Column | Type |
|---|---|
| `id` | integer |
| `trained_at` | timestamp without time zone |
| `feature_names` | jsonb |
| `coefficients` | jsonb |
| `intercept` | double precision |
| `n_train` | integer |
| `n_test` | integer |
| `train_auc` | double precision |
| `test_auc` | double precision |
| `is_deployed` | boolean |
| `notes` | text |

### `model_versions`
| Column | Type |
|---|---|
| `id` | integer |
| `model_name` | text |
| `version` | integer |
| `weights_blob` | bytea |
| `weights_hash` | text |
| `trained_on_n_samples` | integer |
| `held_out_score` | numeric |
| `is_live` | boolean |
| `created_at` | timestamp with time zone |
| `notes` | text |

### `morning_inflows_cache`
| Column | Type |
|---|---|
| `scan_date` | date |
| `payload` | jsonb |
| `saved_at` | timestamp with time zone |

### `morning_watchlist`
| Column | Type |
|---|---|
| `ticker` | character varying |
| `added_at` | timestamp with time zone |
| `notes` | text |

### `multiday_runner_watch`
| Column | Type |
|---|---|
| `id` | integer |
| `d1_date` | date |
| `ticker` | character varying |
| `d1_pct` | double precision |
| `d1_close` | double precision |
| `d1_high` | double precision |
| `d1_low` | double precision |
| `d1_rvol` | double precision |
| `d1_vol` | bigint |
| `d1_strong` | boolean |
| `status` | character varying |
| `d2_date` | date |
| `d2_pct` | double precision |
| `d2_close` | double precision |
| `d2_close_pos` | double precision |
| `d2_above_d1` | boolean |
| `confirmed` | boolean |
| `entry_price` | double precision |
| `stop_price` | double precision |
| `exit_price` | double precision |
| `exit_date` | date |
| `exit_pct` | double precision |
| `hold_days` | integer |
| `exit_reason` | character varying |
| `captured_at` | timestamp with time zone |
| `cap_tier` | character varying |
| `intraday_hit` | boolean |
| `intraday_entry` | double precision |
| `d3_pct` | double precision |
| `d5_pct` | double precision |
| `d10_pct` | double precision |
| `conviction_score` | integer |

### `my_trades`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `strike` | numeric |
| `expiry` | text |
| `vol_oi` | numeric |
| `prem` | bigint |
| `otm_pct` | numeric |
| `urgency` | text |
| `signal_detected_at` | timestamp with time zone |
| `saved_at` | timestamp with time zone |
| `entry_price` | numeric |
| `exit_price` | numeric |
| `contracts` | integer |
| `notes` | text |
| `status` | text |

### `nano_breakout_watchlist`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_date` | date |
| `ticker` | text |
| `price` | numeric |
| `mkt_cap_m` | numeric |
| `breakout_score` | numeric |
| `vol_trend` | numeric |
| `price_vs_high` | numeric |
| `momentum_5d` | numeric |
| `atr_ratio` | numeric |
| `avg_vol_10d` | numeric |
| `notes` | text |
| `created_at` | timestamp with time zone |

### `nano_morning_candidates`
| Column | Type |
|---|---|
| `id` | integer |
| `snap_date` | date |
| `ticker` | character varying |
| `rank` | integer |
| `conviction` | integer |
| `price` | double precision |
| `mcap_m` | double precision |
| `avg_vol` | bigint |
| `accum_pts` | double precision |
| `steady_pts` | double precision |
| `vol_pts` | double precision |
| `mom_pts` | double precision |
| `net_flow_m` | double precision |
| `up_days` | integer |
| `meta` | jsonb |
| `universe_count` | integer |
| `captured_at` | timestamp with time zone |

### `nano_morning_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `pick_date` | date |
| `ticker` | character varying |
| `entry_price` | double precision |
| `stop_price` | double precision |
| `days_held` | integer |
| `max_high` | double precision |
| `max_gain_pct` | double precision |
| `stopped_out` | boolean |
| `final_price` | double precision |
| `final_chg_pct` | double precision |
| `outcome` | character varying |
| `graded_at` | timestamp with time zone |

### `nano_morning_picks`
| Column | Type |
|---|---|
| `id` | integer |
| `pick_date` | date |
| `ticker` | character varying |
| `rank` | integer |
| `entry_price` | double precision |
| `shares` | integer |
| `stop_price` | double precision |
| `cost` | double precision |
| `conviction` | integer |
| `intraday_score` | double precision |
| `rvol15` | double precision |
| `above_vwap` | boolean |
| `verdict` | character varying |
| `meta` | jsonb |
| `created_at` | timestamp with time zone |

### `news_catalyst_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `alert_date` | date |
| `price` | numeric |
| `score` | numeric |
| `catalyst` | text |
| `alerted_at` | timestamp with time zone |

### `oi_daily_snapshot`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | character varying |
| `price` | double precision |
| `strike` | double precision |
| `expiry` | date |
| `oi` | integer |
| `otm_pct` | double precision |
| `days_out` | integer |
| `iv` | double precision |
| `snapshot_date` | date |
| `created_at` | timestamp with time zone |

### `owner_email_log`
| Column | Type |
|---|---|
| `id` | integer |
| `kind` | text |
| `slot` | text |
| `sent_date` | date |
| `sent_at` | timestamp with time zone |

### `polygon_market_daily`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_date` | date |
| `ticker` | character varying |
| `close_price` | double precision |
| `open_price` | double precision |
| `high_price` | double precision |
| `low_price` | double precision |
| `vwap` | double precision |
| `volume` | bigint |
| `prev_close` | double precision |
| `gap_pct` | double precision |
| `rvol` | double precision |
| `close_strength` | double precision |
| `range_pct` | double precision |

### `polygon_rvol_scan`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_date` | date |
| `ticker` | character varying |
| `price` | double precision |
| `open_price` | double precision |
| `high` | double precision |
| `low` | double precision |
| `vwap` | double precision |
| `gap_pct` | double precision |
| `volume` | bigint |
| `avg_volume` | bigint |
| `rvol` | double precision |
| `close_strength` | double precision |

### `position_monitor`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `direction` | text |
| `entry_price` | numeric |
| `strike` | numeric |
| `expiry` | text |
| `email_source` | text |
| `logged_at` | timestamp with time zone |
| `status` | text |
| `exit_alerted_at` | timestamp with time zone |
| `exit_reason` | text |

### `pre_move_templates`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `move_date` | date |
| `move_pct` | numeric |
| `days_before` | integer |
| `avg_gap` | numeric |
| `avg_rvol` | numeric |
| `avg_cs` | numeric |
| `cs_accel` | numeric |
| `vol_accel_5d` | numeric |
| `vol_accel_10d` | numeric |
| `price_mom_5d` | numeric |
| `price_mom_10d` | numeric |
| `avg_range` | numeric |
| `range_comp` | numeric |
| `days_positive` | integer |
| `vwap_above` | numeric |
| `high_prog` | numeric |
| `gap_count` | integer |
| `computed_at` | timestamp with time zone |

### `quant_agent_sessions`
| Column | Type |
|---|---|
| `job_id` | text |
| `question` | text |
| `status` | text |
| `answer` | text |
| `error` | text |
| `created_at` | timestamp with time zone |
| `updated_at` | timestamp with time zone |

### `questions`
| Column | Type |
|---|---|
| `id` | integer |
| `question_number` | integer |
| `category` | text |
| `text` | text |
| `options` | jsonb |
| `correct_letter` | text |
| `explanation` | text |
| `created_at` | timestamp with time zone |
| `question_type` | text |
| `image_url` | text |

### `regime_flags`
| Column | Type |
|---|---|
| `id` | integer |
| `signal_name` | text |
| `flagged_at` | timestamp with time zone |
| `flag_type` | text |
| `severity` | text |
| `details` | jsonb |
| `acknowledged` | boolean |
| `acknowledged_at` | timestamp with time zone |
| `acknowledged_by` | text |

### `retrain_runs`
| Column | Type |
|---|---|
| `id` | integer |
| `model_name` | text |
| `run_at` | timestamp with time zone |
| `n_new_training_examples` | integer |
| `new_version_held_out_metrics` | jsonb |
| `currently_live_held_out_metrics` | jsonb |
| `recommendation` | text |
| `promotion_status` | text |
| `promotion_decided_at` | timestamp with time zone |
| `promotion_decided_by` | text |
| `promotion_notes` | text |
| `serialized_model_blob` | bytea |

### `risk_gate_decisions`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `signal_name` | text |
| `gate_decision` | text |
| `reasons` | jsonb |
| `devils_advocate_argument` | text |
| `cross_signal_agreement_count` | integer |
| `created_at` | timestamp with time zone |

### `rl_policy_versions`
| Column | Type |
|---|---|
| `id` | integer |
| `policy_name` | text |
| `version` | integer |
| `q_table_blob` | bytea |
| `trained_on_n_episodes` | integer |
| `held_out_avg_reward` | numeric |
| `is_live` | boolean |
| `created_at` | timestamp with time zone |

### `sc_morning_candidates`
| Column | Type |
|---|---|
| `id` | integer |
| `snap_date` | date |
| `ticker` | character varying |
| `rank` | integer |
| `conviction` | integer |
| `price` | double precision |
| `mcap_m` | double precision |
| `avg_vol` | bigint |
| `accum_pts` | double precision |
| `steady_pts` | double precision |
| `vol_pts` | double precision |
| `mom_pts` | double precision |
| `opt_pts` | double precision |
| `net_flow_m` | double precision |
| `up_days` | integer |
| `double_signal` | boolean |
| `eod_accum_score` | double precision |
| `meta` | jsonb |
| `universe_count` | integer |
| `captured_at` | timestamp with time zone |
| `precoil_score` | integer |
| `precoil_grade` | character varying |

### `sc_morning_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `pick_date` | date |
| `ticker` | character varying |
| `entry_price` | double precision |
| `stop_price` | double precision |
| `days_held` | integer |
| `max_high` | double precision |
| `max_gain_pct` | double precision |
| `stopped_out` | boolean |
| `final_price` | double precision |
| `final_chg_pct` | double precision |
| `outcome` | character varying |
| `graded_at` | timestamp with time zone |

### `sc_morning_picks`
| Column | Type |
|---|---|
| `id` | integer |
| `pick_date` | date |
| `ticker` | character varying |
| `rank` | integer |
| `entry_price` | double precision |
| `shares` | integer |
| `stop_price` | double precision |
| `cost` | double precision |
| `conviction` | integer |
| `intraday_score` | double precision |
| `rvol15` | double precision |
| `above_vwap` | boolean |
| `opt_pts` | double precision |
| `double_signal` | boolean |
| `verdict` | character varying |
| `meta` | jsonb |
| `created_at` | timestamp with time zone |

### `scan_history`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_time` | timestamp with time zone |
| `scan_date` | date |
| `ticker` | text |
| `price` | numeric |
| `prev_close` | numeric |
| `price_chg_pct` | numeric |
| `gap_pct` | numeric |
| `momentum_open` | numeric |
| `exhaustion_ratio` | numeric |
| `fade_risk` | text |
| `rel_vol` | numeric |
| `today_vol` | bigint |
| `avg_vol` | bigint |
| `inflow_m` | numeric |
| `outflow_m` | numeric |
| `net_m` | numeric |
| `flow_ratio` | numeric |
| `standout_score` | numeric |
| `mkt_cap_m` | numeric |
| `rank_in_scan` | integer |

### `scan_result_cache`
| Column | Type |
|---|---|
| `endpoint` | text |
| `scan_date` | date |
| `payload` | jsonb |
| `updated_at` | timestamp with time zone |

### `score_history`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `score` | integer |
| `price` | numeric |
| `scanned_at` | timestamp with time zone |

### `sessions`
| Column | Type |
|---|---|
| `id` | integer |
| `session_id` | text |
| `questions_answered` | integer |
| `is_subscribed` | boolean |
| `subscription_end_date` | timestamp with time zone |
| `email` | text |
| `created_at` | timestamp with time zone |
| `updated_at` | timestamp with time zone |
| `stripe_customer_id` | text |
| `stripe_subscription_id` | text |
| `referral_code` | text |

### `shadow_positions`
| Column | Type |
|---|---|
| `id` | integer |
| `signal_name` | text |
| `hypothesis_id` | integer |
| `ticker` | text |
| `direction` | text |
| `entry_price` | numeric |
| `entry_time` | timestamp with time zone |
| `exit_price` | numeric |
| `exit_time` | timestamp with time zone |
| `status` | text |
| `notes` | text |
| `raw_signal_payload` | jsonb |
| `created_at` | timestamp with time zone |

### `shadow_promotion_windows`
| Column | Type |
|---|---|
| `id` | integer |
| `signal_name` | text |
| `window_start` | date |
| `window_end` | date |
| `min_trades_required` | integer |
| `promoted` | boolean |
| `promoted_at` | timestamp with time zone |
| `promotion_decision_notes` | text |

### `signal_fire_log`
| Column | Type |
|---|---|
| `id` | integer |
| `signal_name` | character varying |
| `ticker` | character varying |
| `fire_date` | date |
| `fire_price` | double precision |
| `metadata` | jsonb |
| `graded` | boolean |
| `fwd_ret_3d` | double precision |
| `fwd_ret_5d` | double precision |
| `fwd_ret_10d` | double precision |
| `logged_at` | timestamp without time zone |

### `signal_history`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `signal_date` | date |
| `comp_score` | double precision |
| `smart_cp` | double precision |
| `call_verdict` | text |
| `dp_prem_m` | double precision |
| `iv_rank` | double precision |
| `created_at` | timestamp with time zone |

### `signal_outcomes`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `signal_date` | date |
| `session` | text |
| `price_at_signal` | real |
| `call_put_ratio` | real |
| `premium_m` | real |
| `strike` | real |
| `expiry` | text |
| `created_at` | timestamp without time zone |
| `t3_price` | real |
| `t5_price` | real |
| `t10_price` | real |
| `t3_pct` | real |
| `t5_pct` | real |
| `t10_pct` | real |
| `t3_win` | boolean |
| `t5_win` | boolean |
| `t10_win` | boolean |
| `outcomes_updated_at` | timestamp without time zone |

### `signal_trust_history`
| Column | Type |
|---|---|
| `id` | integer |
| `signal_name` | text |
| `context_bucket` | text |
| `trust_weight` | numeric |
| `rolling_win_rate` | numeric |
| `recorded_at` | timestamp with time zone |

### `signal_trust_weights`
| Column | Type |
|---|---|
| `id` | integer |
| `signal_name` | text |
| `context_bucket` | text |
| `rolling_win_rate` | numeric |
| `n_outcomes_observed` | integer |
| `trust_weight` | numeric |
| `last_updated_at` | timestamp with time zone |

### `sm_subscribers`
| Column | Type |
|---|---|
| `id` | integer |
| `email` | character varying |
| `token` | character varying |
| `created_at` | timestamp without time zone |
| `active` | boolean |
| `stripe_customer_id` | text |
| `stripe_subscription_id` | text |
| `paid` | boolean |

### `sms_alerts_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `alert_date` | date |
| `price` | numeric |
| `chg_pct` | numeric |
| `rel_vol` | numeric |
| `score` | numeric |
| `reason` | text |
| `sent_at` | timestamp with time zone |

### `sms_exit_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `exit_date` | date |
| `price` | numeric |
| `chg_pct` | numeric |
| `vwap` | numeric |
| `entry_price` | numeric |
| `sent_at` | timestamp with time zone |

### `sms_midday_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `alert_date` | date |
| `alert_type` | text |
| `price` | numeric |
| `chg_pct` | numeric |
| `rel_vol` | numeric |
| `score` | numeric |
| `sent_at` | timestamp with time zone |

### `sms_profit_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `profit_date` | date |
| `price` | numeric |
| `gain_pct` | numeric |
| `entry_price` | numeric |
| `sent_at` | timestamp with time zone |

### `steady_grinder_scan`
| Column | Type |
|---|---|
| `id` | integer |
| `scan_date` | date |
| `ticker` | text |
| `price` | double precision |
| `d1_date` | date |
| `d1_close` | double precision |
| `d1_low` | double precision |
| `d1_high` | double precision |
| `d1_pct` | double precision |
| `d1_close_pos` | double precision |
| `d1_volume` | bigint |
| `d2_date` | date |
| `d2_close` | double precision |
| `d2_low` | double precision |
| `d2_high` | double precision |
| `d2_pct` | double precision |
| `d2_close_pos` | double precision |
| `d2_volume` | bigint |
| `higher_low` | boolean |
| `dark_pool_pct` | double precision |
| `dark_pool_signal` | text |
| `score` | double precision |
| `created_at` | timestamp with time zone |

### `ticker_meta`
| Column | Type |
|---|---|
| `ticker` | text |
| `quote_type` | text |
| `status` | text |
| `classified_at` | timestamp with time zone |

### `trade_watchlist`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `strike` | numeric |
| `expiry` | text |
| `option_type` | text |
| `entry_price` | numeric |
| `contracts` | integer |
| `notes` | text |
| `saved_at` | timestamp with time zone |

### `unusual_calls_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `price` | numeric |
| `strike` | numeric |
| `expiry` | text |
| `days_out` | integer |
| `volume` | integer |
| `oi` | integer |
| `vol_oi` | numeric |
| `prem` | bigint |
| `otm_pct` | numeric |
| `iv` | numeric |
| `urgency` | text |
| `first_seen` | timestamp with time zone |
| `last_seen` | timestamp with time zone |

### `unusual_calls_microcap_log`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `price` | numeric |
| `strike` | numeric |
| `expiry` | text |
| `days_out` | integer |
| `volume` | integer |
| `oi` | integer |
| `vol_oi` | numeric |
| `prem` | bigint |
| `otm_pct` | numeric |
| `iv` | numeric |
| `urgency` | text |
| `cap_tier` | text |
| `first_seen` | timestamp with time zone |
| `last_seen` | timestamp with time zone |
| `far_otm_sweep` | boolean |

### `vix_daily`
| Column | Type |
|---|---|
| `scan_date` | date |
| `vix_close` | double precision |

### `watched_positions`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | character varying |
| `entry_date` | date |
| `entry_price` | double precision |
| `entry_reason` | text |
| `status` | character varying |
| `exit_date` | date |
| `exit_price` | double precision |
| `exit_reason` | text |
| `created_at` | timestamp without time zone |

### `whale_blocks`
| Column | Type |
|---|---|
| `id` | integer |
| `ticker` | text |
| `direction` | text |
| `strike` | numeric |
| `expiry` | text |
| `days_out` | integer |
| `prem_m` | numeric |
| `volume` | integer |
| `otm_pct` | numeric |
| `category` | text |
| `tier` | text |
| `price` | numeric |
| `first_seen` | timestamp with time zone |

---
## 6. Frontend Structure

| File | Purpose |
|---|---|
| `src/App.tsx` | |
| `src/components/InstitutionalConvictionScore.tsx` | |
| `src/components/ui/accordion.tsx` | |
| `src/components/ui/alert-dialog.tsx` | |
| `src/components/ui/alert.tsx` | |
| `src/components/ui/aspect-ratio.tsx` | |
| `src/components/ui/avatar.tsx` | |
| `src/components/ui/badge.tsx` | |
| `src/components/ui/breadcrumb.tsx` | |
| `src/components/ui/button-group.tsx` | |
| `src/components/ui/button.tsx` | |
| `src/components/ui/calendar.tsx` | |
| `src/components/ui/card.tsx` | |
| `src/components/ui/carousel.tsx` | |
| `src/components/ui/chart.tsx` | |
| `src/components/ui/checkbox.tsx` | |
| `src/components/ui/collapsible.tsx` | |
| `src/components/ui/command.tsx` | |
| `src/components/ui/context-menu.tsx` | |
| `src/components/ui/dialog.tsx` | |
| `src/components/ui/drawer.tsx` | |
| `src/components/ui/dropdown-menu.tsx` | |
| `src/components/ui/empty.tsx` | |
| `src/components/ui/field.tsx` | |
| `src/components/ui/form.tsx` | |
| `src/components/ui/hover-card.tsx` | |
| `src/components/ui/input-group.tsx` | |
| `src/components/ui/input-otp.tsx` | |
| `src/components/ui/input.tsx` | |
| `src/components/ui/item.tsx` | |
| `src/components/ui/kbd.tsx` | |
| `src/components/ui/label.tsx` | |
| `src/components/ui/menubar.tsx` | |
| `src/components/ui/navigation-menu.tsx` | |
| `src/components/ui/pagination.tsx` | |
| `src/components/ui/popover.tsx` | |
| `src/components/ui/progress.tsx` | |
| `src/components/ui/radio-group.tsx` | |
| `src/components/ui/resizable.tsx` | |
| `src/components/ui/scroll-area.tsx` | |
| `src/components/ui/select.tsx` | |
| `src/components/ui/separator.tsx` | |
| `src/components/ui/sheet.tsx` | |
| `src/components/ui/sidebar.tsx` | |
| `src/components/ui/skeleton.tsx` | |
| `src/components/ui/slider.tsx` | |
| `src/components/ui/sonner.tsx` | |
| `src/components/ui/spinner.tsx` | |
| `src/components/ui/switch.tsx` | |
| `src/components/ui/table.tsx` | |
| `src/components/ui/tabs.tsx` | |
| `src/components/ui/textarea.tsx` | |
| `src/components/ui/toast.tsx` | |
| `src/components/ui/toaster.tsx` | |
| `src/components/ui/toggle-group.tsx` | |
| `src/components/ui/toggle.tsx` | |
| `src/components/ui/tooltip.tsx` | |
| `src/hooks/use-mobile.tsx` | |
| `src/hooks/use-toast.ts` | |
| `src/lib/api.ts` | |
| `src/lib/utils.ts` | |
| `src/main.tsx` | |
| `src/pages/Dashboard.tsx` | |
| `src/pages/Landing.tsx` | |
| `src/pages/not-found.tsx` | |