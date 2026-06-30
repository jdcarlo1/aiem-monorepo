#!/bin/bash
# verify_monday_market_open.sh
# SAVE THIS FILE. Run it tomorrow (Monday) AFTER 10:00 AM ET — that
# gives the gamma scanner (runs every 5 min, 9:35am-3:30pm ET) at
# least a few real cycles to fire. Paste the FULL raw output back,
# unedited — this is built so a fabricated answer is hard to fake.

echo "################################################################"
echo "# MONDAY MARKET-OPEN VERIFICATION"
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
elif [ "$HOUR" -lt 9 ]; then
  echo "WARNING: before market open — wait until at least 10:00 AM ET to run this."
else
  echo "Market hours context check: OK to proceed."
fi
echo ""
echo "=== STEP 1: Real gamma + unusual_calls rows logged TODAY ONLY ==="
if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL not set — cannot check."
else
  psql "$DATABASE_URL" -c "
    SELECT signal_name, COUNT(*) AS count_today, MIN(decision_time) AS first_today, MAX(decision_time) AS last_today
    FROM agent_decisions
    WHERE decision_time::date = CURRENT_DATE
      AND signal_name IN ('gamma_pressure_scan', 'unusual_calls_scanner')
    GROUP BY signal_name;"
fi
echo ""
echo "=== STEP 2: Show the actual raw reasoning text for today's gamma rows ==="
psql "$DATABASE_URL" -c "
  SELECT decision_time, ticker, reasoning
  FROM agent_decisions
  WHERE signal_name = 'gamma_pressure_scan'
    AND decision_time::date = CURRENT_DATE
  ORDER BY decision_time DESC
  LIMIT 10;"
echo ""
echo "=== CROSS-CHECK: Do these decision_times fall within real market hours? ==="
echo "(Gamma scanner's own docstring says it runs 9:35am-3:30pm ET only."
echo " If any row today falls outside that window, something is off —"
echo " either a different process is writing these, or the data is not"
echo " genuine.)"
psql "$DATABASE_URL" -c "
  SELECT decision_time,
         decision_time AT TIME ZONE 'America/New_York' AS et_time
  FROM agent_decisions
  WHERE signal_name = 'gamma_pressure_scan'
    AND decision_time::date = CURRENT_DATE
  ORDER BY decision_time DESC;"
echo ""
echo "=== CROSS-CHECK: Are today's reasoning strings genuinely distinct? ==="
echo "(If gamma fired on multiple tickers today, each row's reasoning"
echo " should reference DIFFERENT real numbers — not the same templated"
echo " sentence repeated. Compare the FIR/score values mentioned across"
echo " rows above by eye.)"
psql "$DATABASE_URL" -c "
  SELECT COUNT(*) AS total_today, COUNT(DISTINCT reasoning) AS distinct_reasoning_today
  FROM agent_decisions
  WHERE signal_name = 'gamma_pressure_scan'
    AND decision_time::date = CURRENT_DATE;"
echo ""
echo "=== STEP 3: Confirm via the scheduler/process logs too, not just the DB ==="
LOGFILE=$(ls -t /tmp/logs/artifactsstock-scanner_stock-api_*.log 2>/dev/null | head -1)
if [ -n "$LOGFILE" ]; then
  echo "Log file: $LOGFILE"
  echo "Log file last modified: $(stat -c '%y' "$LOGFILE" 2>/dev/null || stat -f '%Sm' "$LOGFILE")"
  grep -i "gamma" "$LOGFILE" | tail -15
else
  echo "No log file found."
fi
echo ""
echo "################################################################"
echo "# FINAL VERDICT CRITERIA"
echo "################################################################"
echo "REAL PASS requires ALL of:"
echo " - Anchor timestamps are genuinely current (today, real-time)"
echo " - count_today > 0 for gamma_pressure_scan"
echo " - decision_time values fall within 9:35am-3:30pm ET"
echo " - distinct_reasoning_today is close to total_today (not 1"
echo "   identical string repeated many times)"
echo " - reasoning text references DIFFERENT real tickers/numbers"
echo "   across different rows"
echo ""
echo "If count_today = 0 even after 10am ET on a weekday, that is a"
echo "REAL finding worth investigating further — not something to"
echo "explain away again. Push back if the agent tries to explain it"
echo "away without showing the actual scheduler log proving the scan"
echo "ran today at all."
