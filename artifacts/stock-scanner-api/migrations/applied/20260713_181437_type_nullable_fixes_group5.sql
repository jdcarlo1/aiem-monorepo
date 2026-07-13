-- GROUP 5: Fix type/nullable mismatches in dev to match prod
-- Authorized by Joel, schema drift remediation 2026-07-13
-- All type-change tables confirmed 0 dev rows. NOT NULL drops are relaxations only.

-- aiem_supervisor_loop_audit: relax NOT NULL → nullable (match prod)
ALTER TABLE aiem_supervisor_loop_audit ALTER COLUMN audit_trace_id DROP NOT NULL;
ALTER TABLE aiem_supervisor_loop_audit ALTER COLUMN verdict DROP NOT NULL;

-- quant_agent_sessions: relax NOT NULL → nullable (match prod)
ALTER TABLE quant_agent_sessions ALTER COLUMN status     DROP NOT NULL;
ALTER TABLE quant_agent_sessions ALTER COLUMN created_at DROP NOT NULL;
ALTER TABLE quant_agent_sessions ALTER COLUMN updated_at DROP NOT NULL;

-- aiem_prediction_outcomes: align types to prod (0 dev rows, safe)
ALTER TABLE aiem_prediction_outcomes ALTER COLUMN ticker   TYPE TEXT;
ALTER TABLE aiem_prediction_outcomes ALTER COLUMN t1_return TYPE DOUBLE PRECISION USING t1_return::double precision;
ALTER TABLE aiem_prediction_outcomes ALTER COLUMN t3_return TYPE DOUBLE PRECISION USING t3_return::double precision;
ALTER TABLE aiem_prediction_outcomes ALTER COLUMN t5_return TYPE DOUBLE PRECISION USING t5_return::double precision;

-- aiem_process_predictions: align types to prod (0 dev rows, safe)
ALTER TABLE aiem_process_predictions ALTER COLUMN ticker         TYPE VARCHAR;
ALTER TABLE aiem_process_predictions ALTER COLUMN predicted_move TYPE TEXT USING predicted_move::text;
