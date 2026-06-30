#!/bin/bash
# verify_watch_criteria_scan.sh
# SAVE THIS FILE. Run it on the NEXT weekday AFTER 10:00 AM ET — that
# gives premarket_scan (runs every 15 min, 7:00-9:30am ET) and
# missed_morning_check (9:45am ET) at least several real cycles to fire.
# Paste the FULL raw output back, unedited — this is built so a
# fabricated answer is hard to fake.
#
# What this checks: the "missed-runner watch criteria" feature
# (aiem_watch_criteria + aiem_watch_alerts in aiem_autonomous.py) that
# re-screens the live morning universe for yesterday's missed-runner
# pattern, AND the flood-fix that caps/coalesces matches so it can no
# longer return ~1700+ alerts in one run.

echo "################################################################"
echo "# WATCH-CRITERIA PROSPECTIVE SCAN VERIFICATION"
echo "################################################################"
echo ""
echo "=== ANCHOR 1: Real-time proof this is running NOW, live ==="
echo "Shell time: $(date)"
echo "ET time: $(TZ=America/New_York date)"
if [ -n "$DATABASE_URL" ]; then
  psql "$DATABASE_URL" -c "SELECT NOW() AS db_server_time;"
fi
echo "(These three timestamps should all be within seconds of each"
echo " other and should show TODAY's real date — if any of them look"
echo " stale, wrong, or oddly formatted, be suspicious.)"
echo ""
echo "=== ANCHOR 2: Has the market actually been open today? ==="
DOW=$(TZ=America/New_York date +%u)
HOUR=$(TZ=America/New_York date +%H)
echo "Day of week (1=Mon...7=Sun): $DOW, ET hour: $HOUR"
if [ "$DOW" -ge 6 ]; then
  echo "WARNING: today is a weekend — this test is not meaningful, wait for a weekday."
elif [ "$HOUR" -lt 10 ]; then
  echo "WARNING: before 10:00 AM ET — premarket_scan/missed_morning_check may not have"
  echo "had enough cycles yet. Wait until at least 10:00 AM ET to run this."
else
  echo "Market hours context check: OK to proceed."
fi
echo ""
echo "=== STEP 1: Did the scheduled jobs that drive this feature actually fire today? ==="
if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL not set — cannot check."
else
  psql "$DATABASE_URL" -c "
    SELECT job_name, COUNT(*) AS runs_today, MIN(ran_at) AS first_today, MAX(ran_at) AS last_today
    FROM job_log
    WHERE ran_at::date = CURRENT_DATE
      AND job_name IN ('aiem_premarket_scan', 'aiem_missed_morning_check')
    GROUP BY job_name;"
fi
echo "(aiem_premarket_scan should show MULTIPLE runs_today if it's after 7:15am ET,"
echo " since it fires every 15 min from 7:00-9:30am ET. aiem_missed_morning_check"
echo " should show exactly 1 run if it's after 9:45am ET.)"
echo ""
echo "=== STEP 2: Are there any active watch criteria for today's scan to check against? ==="
psql "$DATABASE_URL" -c "
  SELECT id, discovered_date, expires_at, origin_ticker, metric_name, operator,
         threshold_value, observed_value, validation_n, validation_win_rate
  FROM aiem_watch_criteria
  WHERE active = TRUE AND expires_at >= CURRENT_DATE
  ORDER BY discovered_date DESC;"
echo "(If this is empty, the scan has nothing to check and 0 alerts today is"
echo " EXPECTED, not a bug — criteria only get created when yesterday's EOD"
echo " report found a missed runner with an explainable precursor.)"
echo ""
echo "=== STEP 3: Real alert rows written TODAY, grouped by job ==="
psql "$DATABASE_URL" -c "
  SELECT job_name, COUNT(*) AS alerts_today, COUNT(DISTINCT ticker) AS distinct_tickers,
         MIN(sent_at) AS first_alert, MAX(sent_at) AS last_alert
  FROM aiem_watch_alerts
  WHERE alert_date = CURRENT_DATE
  GROUP BY job_name;"
echo "(*** REGRESSION CHECK for the flood fix ***: alerts_today per job_name"
echo " must be <= 25 (_WATCH_MAX_ALERTS_PER_RUN). On the FIRST live dry-run"
echo " before this fix, one call returned 1775 alerts from only 18 active"
echo " criteria — if you see triple-digit+ counts here, the cap regressed.)"
echo ""
echo "=== STEP 4: Show the actual alert rows — real tickers/values, not a template ==="
psql "$DATABASE_URL" -c "
  SELECT a.ticker, c.metric_name, c.operator, c.threshold_value, a.observed_value,
         c.origin_ticker, c.origin_move_pct, a.job_name, a.sent_at
  FROM aiem_watch_alerts a
  JOIN aiem_watch_criteria c ON c.id = a.criteria_id
  WHERE a.alert_date = CURRENT_DATE
  ORDER BY a.sent_at DESC
  LIMIT 30;"
echo "(Cross-check: observed_value should differ per ticker and should clear"
echo " threshold_value in the direction of operator. origin_ticker/origin_move_pct"
echo " identify which past missed-runner this criterion came from.)"
echo ""
echo "=== STEP 5: Dedupe integrity — no criteria_id+ticker+alert_date repeats ==="
psql "$DATABASE_URL" -c "
  SELECT criteria_id, ticker, alert_date, COUNT(*) AS dupe_count
  FROM aiem_watch_alerts
  WHERE alert_date = CURRENT_DATE
  GROUP BY criteria_id, ticker, alert_date
  HAVING COUNT(*) > 1;"
echo "(This should return ZERO rows — the UNIQUE constraint on"
echo " (criteria_id, ticker, alert_date) makes a real duplicate impossible."
echo " Any row here means the schema/constraint itself is broken.)"
echo ""
echo "=== STEP 6: Confirm via the process logs too, not just the DB ==="
LOGFILE=$(ls -t /tmp/logs/artifactsstock-scanner_aiem-process_*.log 2>/dev/null | head -1)
if [ -n "$LOGFILE" ]; then
  echo "Log file: $LOGFILE"
  echo "Log file last modified: $(stat -c '%y' "$LOGFILE" 2>/dev/null || stat -f '%Sm' "$LOGFILE")"
  grep -i "watch_scan" "$LOGFILE" | tail -20
else
  echo "No aiem-process log file found."
fi
echo "(Look for lines like '[watch_scan] aiem_premarket_scan: N new pattern-match"
echo " alert(s) sent' or '...0 new matches across N active criteria'. If a run"
echo " logs 'capping to top 25 by margin', that confirms the cap engaged for"
echo " real on a real over-threshold run, rather than just sitting unused.)"
echo ""
echo "################################################################"
echo "# FINAL VERDICT CRITERIA"
echo "################################################################"
echo "REAL PASS requires ALL of:"
echo " - Anchor timestamps are genuinely current (today, real-time)"
echo " - runs_today > 0 for aiem_premarket_scan (and for aiem_missed_morning_check"
echo "   if after 9:45am ET) in STEP 1"
echo " - alerts_today per job_name in STEP 3 is 0-25 (never above 25)"
echo " - STEP 4 rows show genuinely different tickers/observed_values, not one"
echo "   templated row repeated"
echo " - STEP 5 returns ZERO duplicate rows"
echo " - STEP 6 log lines exist and match the DB counts from STEP 3"
echo ""
echo "If STEP 2 shows active criteria but STEP 3 shows 0 alerts for both jobs,"
echo "that's plausible (no live match today) but worth noting — it does NOT"
echo "by itself prove the scan ran; STEP 1 + STEP 6 are what prove it ran."
echo "If alerts_today ever exceeds 25, that is a REAL regression of the flood"
echo "fix — investigate _WATCH_MAX_ALERTS_PER_RUN / the coalesce step in"
echo "_aiem_scan_watch_criteria() before explaining it away."
