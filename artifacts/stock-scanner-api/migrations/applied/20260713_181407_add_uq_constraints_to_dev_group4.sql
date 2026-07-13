-- GROUP 4: Add missing UNIQUE constraints to dev (prevents Replit from dropping them from prod)
-- Authorized by Joel, schema drift remediation 2026-07-13
-- All tables confirmed 0 or 1 rows in dev before execution.

ALTER TABLE aiem_probability_engine_pit_corrections ADD UNIQUE (original_prediction_id);
ALTER TABLE aiem_process_outcomes                   ADD UNIQUE (prediction_date, ticker);
ALTER TABLE aiem_sector_alerts_log                  ADD UNIQUE (date, sector_ticker);
ALTER TABLE aiem_supervisor_performance_reports     ADD UNIQUE (period_start, period_type);
ALTER TABLE aiem_verification_log                   ADD UNIQUE (job_id, verified_at);
ALTER TABLE aiem_watch_alerts                       ADD UNIQUE (alert_date, criteria_id, ticker);
ALTER TABLE dc_template_feedback                    ADD UNIQUE (discovery_id, verdict);
ALTER TABLE earnings_calendar                       ADD UNIQUE (earnings_date, ticker);
ALTER TABLE eod_outcomes                            ADD UNIQUE (ticker, trade_date);
ALTER TABLE gspc_daily                              ADD UNIQUE (scan_date);
ALTER TABLE scan_history                            ADD UNIQUE (scan_time, ticker);
ALTER TABLE ai_short_calls_log                      ADD UNIQUE (pick_id);
ALTER TABLE aiem_paper_trades                       ADD UNIQUE (ticker, trade_date, trade_type);
