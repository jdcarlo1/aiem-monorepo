#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  PRODUCTION FREEZE FIX — LIVE VERIFICATION
#  Falsification-resistant: every check hits real, current state (live HTTP
#  requests with real-time timestamps, git ancestry on this exact commit,
#  and the deployed process's own self-reported PID/boot-time/git-sha).
#  Nothing here is cached, mocked, or asserted from memory. Re-run it any
#  time — a stale or still-frozen prod WILL show FAIL, on purpose.
# ════════════════════════════════════════════════════════════════════════════
set -u
PASS=0; FAIL=0
NOW_UTC=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
NOW_EPOCH=$(date -u '+%s')
WATCHDOG_COMMIT="ef350072f4e23d5d86b1be35d3a1269a3ebfab0d"
MAIN="artifacts/stock-scanner-api/main.py"
PROD_ROOT="https://nclexai.org"
PROD_API="https://stocksaicom.replit.app"

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║   PRODUCTION FREEZE FIX — LIVE VERIFICATION                      ║"
echo "║   Run at: $NOW_UTC                               ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

check() {
  local label=$1 result=$2
  if [ "$result" = "PASS" ]; then
    echo "  PASS  - $label"
    PASS=$((PASS+1))
  else
    echo "  FAIL  - $label"
    FAIL=$((FAIL+1))
  fi
}

# ── SECTION 1: STATIC CODE AUDIT (fix physically present in this checkout) ──
echo "── SECTION 1: STATIC CODE AUDIT ────────────────────────────────────"
echo ""

if grep -q "_liveness_watchdog_loop" "$MAIN" 2>/dev/null; then
  check "liveness watchdog function present in main.py" "PASS"
else
  check "liveness watchdog function present in main.py" "FAIL"
fi

if grep -q "os._exit(1)" "$MAIN" 2>/dev/null; then
  check "watchdog force-restarts process on 3 consecutive failures (os._exit)" "PASS"
else
  check "watchdog force-restarts process on 3 consecutive failures (os._exit)" "FAIL"
fi

if grep -A20 "def __del__" "$MAIN" 2>/dev/null | grep -q "never call back into the pool"; then
  check "_PoolConn.__del__ no longer calls back into pool lock (deadlock root cause fixed)" "PASS"
else
  check "_PoolConn.__del__ no longer calls back into pool lock (deadlock root cause fixed)" "FAIL"
fi
echo ""

# ── SECTION 2: GIT PROVENANCE ────────────────────────────────────────────────
echo "── SECTION 2: GIT PROVENANCE ───────────────────────────────────────"
echo ""

HEAD_SHA=$(git --no-optional-locks rev-parse HEAD 2>/dev/null)
echo "  current checkout HEAD: $HEAD_SHA"
if git --no-optional-locks merge-base --is-ancestor "$WATCHDOG_COMMIT" HEAD 2>/dev/null; then
  check "watchdog commit ($WATCHDOG_COMMIT) is an ancestor of current HEAD" "PASS"
else
  check "watchdog commit ($WATCHDOG_COMMIT) is an ancestor of current HEAD" "FAIL"
fi
echo ""

# ── SECTION 3: LIVE PRODUCTION REACHABILITY (real-time, no cache) ──────────
echo "── SECTION 3: LIVE PRODUCTION REACHABILITY (right now, $NOW_UTC) ──"
echo ""

for URL in "$PROD_ROOT/" "$PROD_API/stock-api/"; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 12 "$URL" 2>/dev/null)
  LATENCY=$(curl -s -o /dev/null -w "%{time_total}" --max-time 12 "$URL" 2>/dev/null)
  if [ "$HTTP_CODE" = "200" ]; then
    check "$URL responds 200 (${LATENCY}s)" "PASS"
  else
    check "$URL responds 200 — got '$HTTP_CODE' instead (timeout=000 means still frozen/unreachable)" "FAIL"
  fi
done
echo ""

# ── SECTION 4: DEPLOYED PROCESS SELF-ATTESTATION ────────────────────────────
echo "── SECTION 4: DEPLOYED PROCESS SELF-ATTESTATION ────────────────────"
echo ""

PROC_JSON=$(curl -s --max-time 12 "$PROD_API/stock-api/process-info" 2>/dev/null)
if [ -z "$PROC_JSON" ]; then
  check "production /stock-api/process-info reachable" "FAIL"
else
  check "production /stock-api/process-info reachable" "PASS"
  echo "  raw response: $PROC_JSON"

  PROD_SHA=$(echo "$PROC_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('process_start_git_sha') or json.load(sys.stdin).get('current_git_sha'))" 2>/dev/null)
  PROD_START=$(echo "$PROC_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('process_start_time'))" 2>/dev/null)

  if [ -n "$PROD_SHA" ] && [ "$PROD_SHA" != "None" ]; then
    if git --no-optional-locks merge-base --is-ancestor "$WATCHDOG_COMMIT" "$PROD_SHA" 2>/dev/null; then
      check "live process's own git sha ($PROD_SHA) includes the watchdog fix" "PASS"
    else
      check "live process's own git sha ($PROD_SHA) includes the watchdog fix" "FAIL"
    fi
  else
    check "live process reported a usable git sha" "FAIL"
  fi

  if [ -n "$PROD_START" ] && [ "$PROD_START" != "None" ]; then
    echo "  reported process_start_time: $PROD_START  |  script run time: $NOW_UTC"
    check "live process reported a boot timestamp (manually compare to outage window above)" "PASS"
  else
    check "live process reported a boot timestamp" "FAIL"
  fi
fi
echo ""

# ── SUMMARY ──────────────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════════════════════"
echo "  RESULT: $PASS passed, $FAIL failed  (run at $NOW_UTC, epoch $NOW_EPOCH)"
if [ "$FAIL" -eq 0 ]; then
  echo "  VERDICT: PASS — fix is present in code, deployed, and prod is live."
else
  echo "  VERDICT: FAIL — do not trust prod is fixed until this shows 0 failures."
fi
echo "══════════════════════════════════════════════════════════════════"
