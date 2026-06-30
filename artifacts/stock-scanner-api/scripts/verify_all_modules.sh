#!/bin/bash
# verify_all_modules.sh
# SAVE THIS FILE. Run AFTER 10:00 AM ET on a weekday — gives the
# scanners several real cycles to fire. Paste the FULL raw output
# back, unedited.
#
# ============================================================
# CONFIG — every value below was checked against the actual source
# code/schema (not guessed). Corrections vs. the original draft are
# called out in comments so you can see what was wrong and why.
# ============================================================

# Confirmed real signal_name values actually written to agent_decisions
# (artifacts/stock-scanner-api/decision_logging_helper.py and friends).
# CORRECTIONS vs. the original guess list:
#   - "charm_scan"      -> actual is "charm_cascade"
#   - "dark_pool_scan"  -> actual is "dark_pool_scanner"
#   - "oi_build_scan", "squeeze_fuel_scan", "float_od_scan",
#     "sweep_scan", "sector_heat_scan" -> NONE of these exist as their
#     own agent_decisions row. OI buildup / squeeze / float / sector-heat
#     are sub-scores (gamma_score, dark_pool_score, squeeze_score,
#     sector_heat_score columns) inside ai_short_calls_log, not
#     independent decisions. Sweep is its own table, call_sweep_log
#     (via options_sweep.py), not agent_decisions. See PART A2/A3 below
#     for the real way to check those instead of guessed signal_names.
SIGNAL_NAMES=(
  "gamma_pressure_scan"
  "charm_cascade"
  "dark_pool_scanner"
  "unusual_calls_scanner"
  "market_regime_overlay"
  "smart_money_divergence"
  "intraday_continuation_scanner"
  "premarket_gap_continuation"
  "premarket_open_trader"
  "pre_recommendation_synthesis"
  "aiem_position_review"
)
# Scanner active window — adjust if different scanners run on
# different schedules.
WINDOW_START="09:35"
WINDOW_END="15:30"

# Confirmed real job logging table (aiem_autonomous.py lines 101-106):
#   CREATE TABLE job_log (id BIGSERIAL, job_name TEXT, ran_at TIMESTAMPTZ)
# CORRECTION: there is no "job_run_log" table and no status column —
# job_log only records that a job ran and when, not pass/fail. Pass/fail
# is tracked separately via record_job_success()/record_job_failure()
# calls scattered through main.py (no single table; check PART E logs).
JOBS_TABLE="job_log"
JOBS_NAME_COL="job_name"
JOBS_TIME_COL="ran_at"
# main.py's apscheduler instance registers 102 jobs (some are dynamically
# generated, e.g. microcap_options_auto_{hour}_{min}), well above the
# 70+/day baseline this script checks for.
EXPECTED_JOB_COUNT_MIN=50

echo "################################################################"
echo "# FULL SYSTEM VERIFICATION — ALL MODULES"
echo "################################################################"
echo ""
echo "=== ANCHOR 1: Real-time proof this is running NOW, live ==="
echo "Shell time: $(date)"
echo "ET time: $(TZ=America/New_York date)"
if [ -n "$DATABASE_URL" ]; then
  psql "$DATABASE_URL" -c "SELECT NOW() AS db_server_time;"
fi
echo ""
echo "=== ANCHOR 2: Has the market actually been open today? ==="
DOW=$(TZ=America/New_York date +%u)
HOUR=$(TZ=America/New_York date +%H)
echo "Day of week (1=Mon...7=Sun): $DOW, ET hour: $HOUR"
if [ "$DOW" -ge 6 ]; then
  echo "WARNING: weekend — not meaningful, wait for a weekday."
elif [ "$HOUR" -lt 9 ]; then
  echo "WARNING: before market open — wait until at least 10:00 AM ET."
else
  echo "Market hours context check: OK to proceed."
fi
echo ""

if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL not set — cannot run DB checks. STOP HERE, this is a real failure."
  exit 1
fi

echo "################################################################"
echo "# PART A1 — SIGNAL SCANNERS LOGGED IN agent_decisions"
echo "################################################################"
for SIG in "${SIGNAL_NAMES[@]}"; do
  echo ""
  echo "--- Module: $SIG ---"
  psql "$DATABASE_URL" -c "
    SELECT COUNT(*) AS count_today,
           MIN(decision_time) AS first_today,
           MAX(decision_time) AS last_today,
           COUNT(DISTINCT reasoning) AS distinct_reasoning_today
    FROM agent_decisions
    WHERE signal_name = '$SIG'
      AND decision_time::date = CURRENT_DATE;"

  echo "Sample raw reasoning (up to 5 rows) for $SIG:"
  psql "$DATABASE_URL" -c "
    SELECT decision_time, ticker, reasoning
    FROM agent_decisions
    WHERE signal_name = '$SIG'
      AND decision_time::date = CURRENT_DATE
    ORDER BY decision_time DESC
    LIMIT 5;"

  echo "Out-of-window rows for $SIG (should be EMPTY if scanner only runs $WINDOW_START-$WINDOW_END ET):"
  psql "$DATABASE_URL" -c "
    SELECT decision_time AT TIME ZONE 'America/New_York' AS et_time
    FROM agent_decisions
    WHERE signal_name = '$SIG'
      AND decision_time::date = CURRENT_DATE
      AND (decision_time AT TIME ZONE 'America/New_York')::time
          NOT BETWEEN '$WINDOW_START' AND '$WINDOW_END';"
done

echo ""
echo "################################################################"
echo "# PART A2 — pre_squeeze_warning / accumulation_breakout"
echo "#           (these live in signal_fire_log, NOT agent_decisions)"
echo "################################################################"
psql "$DATABASE_URL" -c "
  SELECT signal_name, COUNT(*) AS count_today,
         MIN(logged_at) AS first_today, MAX(logged_at) AS last_today,
         COUNT(DISTINCT ticker) AS distinct_tickers_today
  FROM signal_fire_log
  WHERE fire_date = CURRENT_DATE
    AND signal_name IN ('pre_squeeze_warning', 'accumulation_breakout')
  GROUP BY signal_name;"

echo ""
echo "################################################################"
echo "# PART A3 — OI buildup / squeeze / float-OD / sector-heat scores"
echo "#           (these are COLUMNS on ai_short_calls_log, not their"
echo "#            own scanner rows — check today's picks got real,"
echo "#            non-null, non-identical scores)"
echo "################################################################"
psql "$DATABASE_URL" -c "
  SELECT ticker, gamma_score, dark_pool_score, squeeze_score, sector_heat_score, created_at
  FROM ai_short_calls_log
  WHERE created_at::date = CURRENT_DATE
  ORDER BY created_at DESC
  LIMIT 15;"
echo "(If gamma_score/dark_pool_score/squeeze_score/sector_heat_score are"
echo " NULL or identical across every row today, the per-layer scoring"
echo " isn't actually differentiating picks — push back on that.)"
echo ""
echo "Sweep activity today (separate table, call_sweep_log via options_sweep.py):"
psql "$DATABASE_URL" -c "
  SELECT COUNT(*) AS sweeps_today, COUNT(DISTINCT ticker) AS distinct_tickers
  FROM call_sweep_log
  WHERE sweep_date = CURRENT_DATE;" 2>&1

echo ""
echo "################################################################"
echo "# PART B — SCHEDULED JOBS (job_log; expect 70+/day, confirmed 102"
echo "#          jobs registered in main.py's scheduler — some only"
echo "#          fire on specific days, so daily count will be a subset)"
echo "################################################################"
echo "job_log has no status column — this shows job_name + run counts only."
psql "$DATABASE_URL" -c "
  SELECT $JOBS_NAME_COL, COUNT(*) AS runs_today,
         MIN($JOBS_TIME_COL) AS first_today, MAX($JOBS_TIME_COL) AS last_today
  FROM $JOBS_TABLE
  WHERE $JOBS_TIME_COL::date = CURRENT_DATE
  GROUP BY $JOBS_NAME_COL
  ORDER BY runs_today DESC;"

echo ""
echo "Distinct job names that ran today vs. expected minimum:"
psql "$DATABASE_URL" -c "
  SELECT COUNT(DISTINCT $JOBS_NAME_COL) AS distinct_jobs_today
  FROM $JOBS_TABLE
  WHERE $JOBS_TIME_COL::date = CURRENT_DATE;"
echo "If distinct_jobs_today is well below $EXPECTED_JOB_COUNT_MIN on a normal"
echo "weekday, that's a real finding — push the agent for the scheduler log,"
echo "not an explanation. (Note: job_log only gets a row when a job fires; many"
echo "of the 102 registered jobs are weekly/Sunday-only, so a weekday count"
echo "well under 102 is normal — compare against $JOBS_TABLE history, not 102.)"
echo ""

echo "################################################################"
echo "# PART C — AI PIPELINES (confirmed real tables, not guesses)"
echo "################################################################"
echo "--- 1. Conversational AIEM agent: quant_agent_sessions ---"
psql "$DATABASE_URL" -c "
  SELECT status, COUNT(*) AS count_today
  FROM quant_agent_sessions
  WHERE created_at::date = CURRENT_DATE
  GROUP BY status;"
psql "$DATABASE_URL" -c "
  SELECT job_id, question, status, current_tool, created_at
  FROM quant_agent_sessions
  WHERE created_at::date = CURRENT_DATE
  ORDER BY created_at DESC
  LIMIT 5;"

echo ""
echo "--- 2. Autonomous research/discovery loop: hypothesis_registry + job_log ---"
echo "(Research agent job runs Sunday 8 PM ET only — id 'aiem_research_agent' in"
echo " main.py. On a weekday this will show last Sunday's run, not today's — that"
echo " is EXPECTED, not a failure.)"
psql "$DATABASE_URL" -c "
  SELECT job_name, MAX(ran_at) AS last_run
  FROM job_log
  WHERE job_name = 'aiem_research_agent'
  GROUP BY job_name;"
psql "$DATABASE_URL" -c "
  SELECT id, name, test_start, test_end, locked, registered_at
  FROM hypothesis_registry
  ORDER BY registered_at DESC
  LIMIT 10;"

echo ""
echo "--- 3. XGBoost conviction model retrain: aiem_ml_retrain_log ---"
echo "(Real table, written by retrain_pipeline.py::run_retrain_cycle(), called"
echo " from the SAME Sunday 8 PM ET 'aiem_research_agent' job, just before the"
echo " research loop runs. NOTE: there is a SEPARATE older LogisticRegression"
echo " retrain path — job id 'aiem_model_retrain_weekly', Sunday 7 PM ET,"
echo " writing to model_registry — don't confuse the two if asking the agent.)"
psql "$DATABASE_URL" -c "
  SELECT retrain_date, n_samples, candidate_auc, candidate_brier,
         prod_auc, prod_brier, promoted, reason, created_at
  FROM aiem_ml_retrain_log
  ORDER BY created_at DESC
  LIMIT 5;"

echo ""
echo "--- 4. Behavioral fingerprint matcher: pre_move_templates ---"
echo "IMPORTANT: this is a STATIC reference table of historical pre-move"
echo "templates (~2,946 rows per project memory). Live matching happens"
echo "at query time via best_template_match() in behavioral_fingerprint.py"
echo "and is NOT written to any 'matches today' log table. So there is no"
echo "real query that shows 'matches logged today' — if the agent claims"
echo "one exists, that itself is a finding. The only honest check is:"
psql "$DATABASE_URL" -c "
  SELECT COUNT(*) AS template_count, MAX(move_date) AS most_recent_template
  FROM pre_move_templates;"
echo "(template_count should be in the thousands; most_recent_template tells"
echo " you when templates were last rebuilt — rebuild job runs Sunday 5 PM ET.)"

echo ""
echo "--- 5. Bull/bear specialist council: bull_bear_debates ---"
psql "$DATABASE_URL" -c "
  SELECT COUNT(*) AS debates_today, COUNT(DISTINCT ticker) AS distinct_tickers
  FROM bull_bear_debates
  WHERE debate_time::date = CURRENT_DATE;"
psql "$DATABASE_URL" -c "
  SELECT debate_time, ticker, verdict, LEFT(bull_argument, 80) AS bull_snippet,
         LEFT(bear_argument, 80) AS bear_snippet
  FROM bull_bear_debates
  WHERE debate_time::date = CURRENT_DATE
  ORDER BY debate_time DESC
  LIMIT 5;"

echo ""
echo "################################################################"
echo "# PART D — MARKET DATA INTEGRATIONS (Polygon / Tradier / Yahoo)"
echo "################################################################"
echo "CONFIRMED: Tradier (_TD_BREAKER) and Yahoo (_YF_BREAKER) circuit"
echo "breakers are IN-MEMORY ONLY in main.py — there is no DB table for"
echo "trips. The only real evidence is the process log (PART E). Look for:"
echo "  '[td_breaker] tripped: 3+ Tradier timeouts in 30s — cooling 90s'"
echo "  '[breaker] silent-throttle tripped: 15+ empty price/options responses in 30s'"
echo "Polygon has no breaker/error table either — its health is inferred"
echo "from whether today's row exists yet in polygon_market_daily:"
psql "$DATABASE_URL" -c "
  SELECT MAX(scan_date) AS most_recent_polygon_day,
         CURRENT_DATE - MAX(scan_date) AS days_stale
  FROM polygon_market_daily;"
echo "(days_stale of 0-1 on a weekday afternoon is healthy; anything larger"
echo " before EOD is expected since the daily snapshot lands after close —"
echo " but 2+ days stale on a weekday IS a real finding.)"

echo ""
echo "################################################################"
echo "# PART E — PROCESS / SCHEDULER LOGS"
echo "################################################################"
LOGFILE=$(ls -t /tmp/logs/*stock-scanner*stock-api*.log 2>/dev/null | head -1)
if [ -n "$LOGFILE" ]; then
  echo "Log file: $LOGFILE"
  echo "Last modified: $(stat -c '%y' "$LOGFILE" 2>/dev/null || stat -f '%Sm' "$LOGFILE")"
  echo "--- breaker trips today ---"
  grep -iE "td_breaker|silent-throttle tripped|circuit breaker" "$LOGFILE" | tail -10
  echo "--- last 20 lines mentioning any module keyword ---"
  grep -iE "gamma|charm|squeeze|dark.?pool|float.?od|sweep|sector.?heat|oi.?build|retrain|specialist.?council|hypothesis" "$LOGFILE" | tail -20
else
  echo "No log file found matching expected pattern — confirm actual log path/naming with the agent."
fi

echo ""
echo "################################################################"
echo "# FINAL VERDICT CRITERIA"
echo "################################################################"
echo "REAL PASS requires ALL of:"
echo " - Anchor timestamps genuinely current"
echo " - Every scanner in PART A1 has count_today > 0 on a normal trading day,"
echo "   with distinct_reasoning_today close to count_today (not 1 repeated string)"
echo " - Zero out-of-window rows per module in PART A1"
echo " - PART A3 scores are non-null and vary across tickers, not identical"
echo " - PART B distinct_jobs_today is a reasonable fraction of 102 (weekday"
echo "   jobs only — don't expect all 102 on a Tuesday)"
echo " - PART C items 1, 3, 5 show real, distinct rows for TODAY where the"
echo "   underlying job/feature actually runs daily; item 2's research-agent"
echo "   timestamp should be from the most recent Sunday, not stale/missing;"
echo "   item 4 should show a healthy template_count, not a 'matches today'"
echo "   count (none exists by design)"
echo " - PART D polygon days_stale is 0-1 on a normal weekday; breaker trips"
echo "   in PART E (if any) should be sparse, not constant"
echo ""
echo "Any module that comes back empty, templated, or only summarized"
echo "(not raw) is a real finding. Don't let it get explained away without"
echo "the underlying query result or log line to back it up."
