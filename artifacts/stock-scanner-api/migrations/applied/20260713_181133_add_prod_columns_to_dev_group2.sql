-- GROUP 2: Add prod-only columns to dev (prevents column drops from prod on publish)
-- Authorized by Joel, schema drift remediation 2026-07-13

-- ai_short_calls_log (+5 cols: 33→38)
CREATE SEQUENCE IF NOT EXISTS ai_short_calls_log_pick_id_seq;
ALTER TABLE ai_short_calls_log ADD COLUMN IF NOT EXISTS gamma_score NUMERIC;
ALTER TABLE ai_short_calls_log ADD COLUMN IF NOT EXISTS dark_pool_score NUMERIC;
ALTER TABLE ai_short_calls_log ADD COLUMN IF NOT EXISTS squeeze_score NUMERIC;
ALTER TABLE ai_short_calls_log ADD COLUMN IF NOT EXISTS sector_heat_score NUMERIC;
ALTER TABLE ai_short_calls_log ADD COLUMN IF NOT EXISTS pick_id BIGINT NOT NULL DEFAULT nextval('ai_short_calls_log_pick_id_seq'::regclass);

-- aiem_paper_trades (+6 cols: 35→41)
ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS mid_price NUMERIC;
ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS fill_price NUMERIC;
ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS spread_pct_used NUMERIC;
ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS unachievable_fill BOOLEAN DEFAULT false;
ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS illiquid_fill BOOLEAN DEFAULT false;
ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS is_test_data BOOLEAN DEFAULT false;

-- aiem_prediction_outcomes (+4 cols: 9→13)
ALTER TABLE aiem_prediction_outcomes ADD COLUMN IF NOT EXISTS entry_price FLOAT8;
ALTER TABLE aiem_prediction_outcomes ADD COLUMN IF NOT EXISTS t1_price FLOAT8;
ALTER TABLE aiem_prediction_outcomes ADD COLUMN IF NOT EXISTS t3_price FLOAT8;
ALTER TABLE aiem_prediction_outcomes ADD COLUMN IF NOT EXISTS t5_price FLOAT8;

-- aiem_process_predictions (+1 col: 9→10)
ALTER TABLE aiem_process_predictions ADD COLUMN IF NOT EXISTS gap_pct FLOAT8;

-- aiem_research_insights (+1 col: 7→8)
ALTER TABLE aiem_research_insights ADD COLUMN IF NOT EXISTS session_name TEXT;

-- aiem_squeeze_backtest_log (+2 cols: 31→33)
ALTER TABLE aiem_squeeze_backtest_log ADD COLUMN IF NOT EXISTS si_pct_status TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED';
ALTER TABLE aiem_squeeze_backtest_log ADD COLUMN IF NOT EXISTS dtc_status TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED';

-- aiem_squeeze_signals (+4 cols: 22→26)
ALTER TABLE aiem_squeeze_signals ADD COLUMN IF NOT EXISTS si_pct_status TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED';
ALTER TABLE aiem_squeeze_signals ADD COLUMN IF NOT EXISTS borrow_cost FLOAT8;
ALTER TABLE aiem_squeeze_signals ADD COLUMN IF NOT EXISTS dtc FLOAT8;
ALTER TABLE aiem_squeeze_signals ADD COLUMN IF NOT EXISTS dtc_status TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED';

-- aiem_supervisor_loop_audit (+3 cols: 16→19)
ALTER TABLE aiem_supervisor_loop_audit ADD COLUMN IF NOT EXISTS signal_source TEXT;
ALTER TABLE aiem_supervisor_loop_audit ADD COLUMN IF NOT EXISTS supervisor_verdict TEXT NOT NULL DEFAULT 'INCOMPLETE';
ALTER TABLE aiem_supervisor_loop_audit ADD COLUMN IF NOT EXISTS notes TEXT;

-- bull_bear_debates (+2 cols: 10→12)
ALTER TABLE bull_bear_debates ADD COLUMN IF NOT EXISTS candidate_id BIGINT;
ALTER TABLE bull_bear_debates ADD COLUMN IF NOT EXISTS audit_log_id TEXT;

-- model_versions (+4 cols: 10→14)
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS version_label TEXT;
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS deployed_at TIMESTAMPTZ;
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT false;
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS rolled_back_at TIMESTAMPTZ;

-- quant_agent_sessions (+4 cols: 12→16)
ALTER TABLE quant_agent_sessions ADD COLUMN IF NOT EXISTS aiem_signature TEXT;
ALTER TABLE quant_agent_sessions ADD COLUMN IF NOT EXISTS signed_at TIMESTAMPTZ;
ALTER TABLE quant_agent_sessions ADD COLUMN IF NOT EXISTS openai_response_id TEXT;
ALTER TABLE quant_agent_sessions ADD COLUMN IF NOT EXISTS signed_ts TEXT;

-- sc_morning_candidates (+2 cols: 20→22)
ALTER TABLE sc_morning_candidates ADD COLUMN IF NOT EXISTS precoil_score INTEGER;
ALTER TABLE sc_morning_candidates ADD COLUMN IF NOT EXISTS precoil_grade VARCHAR;

-- sm_subscribers (+3 cols: 10→13)
ALTER TABLE sm_subscribers ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;
ALTER TABLE sm_subscribers ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;
ALTER TABLE sm_subscribers ADD COLUMN IF NOT EXISTS paid BOOLEAN DEFAULT false;
