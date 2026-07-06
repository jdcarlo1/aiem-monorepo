#!/usr/bin/env bash
# ============================================================================
# AIEM STRICT WIRING VERIFICATION
# ============================================================================
# Purpose: prove — with raw grep/psql output, not agent self-reports —
# whether each module exists, whether modules are actually WIRED to each
# other (not just present as standalone files), and catch any claim of
# "fully absent" or "zero calls" that doesn't hold up.
#
# Run from repo root:  bash aiem_wiring_verify.sh | tee wiring_report.txt
#
# Every section prints:
#   [CLAIM]  what the agent told Joel
#   [CHECK]  the exact command being run
#   [RAW]    unmodified output
#   [VERDICT] auto-flagged PASS/FAIL based on exit code / output presence
# ============================================================================

set -uo pipefail

PASS=0
FAIL=0

section() {
  echo ""
  echo "════════════════════════════════════════════════════════════════"
  echo "$1"
  echo "════════════════════════════════════════════════════════════════"
}

# Runs a grep, prints raw output, and verdicts based on whether it found
# something (expect_found=1) or should have found nothing (expect_found=0).
check() {
  local label="$1" cmd="$2" expect_found="$3"
  echo ""
  echo "[CLAIM]  $label"
  echo "[CHECK]  $cmd"
  echo "[RAW]"
  local out
  out=$(eval "$cmd" 2>&1)
  local code=$?
  if [ -z "$out" ]; then
    echo "  (no output — exit code $code)"
  else
    echo "$out" | sed 's/^/  /'
  fi
  echo "[VERDICT]"
  if [ "$expect_found" = "1" ]; then
    if [ -n "$out" ]; then echo "  PASS — found as claimed"; PASS=$((PASS+1));
    else echo "  FAIL — claimed present, but nothing found"; FAIL=$((FAIL+1)); fi
  else
    if [ -z "$out" ]; then echo "  PASS — confirmed absent"; PASS=$((PASS+1));
    else echo "  FAIL — claimed absent, but found something (see RAW above)"; FAIL=$((FAIL+1)); fi
  fi
}

# ============================================================================
section "MODULE 7 — Data Source Discovery: claimed FULLY ABSENT"
# ============================================================================
check "No file/function/comment for data source discovery anywhere in repo" \
  "grep -rn 'data_source_discovery\|discover_data_source\|earnings_calendar_gate\|is_earnings_adjacent' . --include='*.py'" \
  0

# ============================================================================
section "MODULE 6 — Scheduling: claimed 9 jobs in aiem_process.py"
# ============================================================================
check "Exactly 9 sched.add_job() calls in aiem_process.py" \
  "grep -n 'sched.add_job' aiem_process.py" \
  1
echo ""
echo "[MANUAL COUNT CHECK] Compare count above to expected list of 9:"
echo "  aiem_warmup, aiem_premarket_scan, aiem_open_watcher, aiem_grade_outcomes,"
echo "  aiem_grade_t3_t5, aiem_find_missed_runners, aiem_pattern_gap_analysis,"
echo "  aiem_write_signal_discoveries, aiem_nightly_learn"
grep -c 'sched.add_job' aiem_process.py 2>/dev/null | sed 's/^/  actual count: /'

check "Confirm main.py ALSO has its own scheduler (don't build a 3rd)" \
  "grep -n 'BackgroundScheduler\|BlockingScheduler\|sched = ' main.py" \
  1

check "Discovery Engine run_cycle() is NOT yet in either scheduler (the gap)" \
  "grep -n 'run_cycle' aiem_process.py main.py" \
  0

# ============================================================================
section "MODULE 3 — Online Learning: claimed NO rolling baseline_wr accumulator"
# ============================================================================
check "No baseline_wr rolling accumulator anywhere" \
  "grep -n 'baseline_wr' *.py" \
  0
check "online_learning.py only touches model weights, not win-rate stats" \
  "grep -n 'win_rate\|rolling_wr' online_learning.py" \
  0

# ============================================================================
section "MODULE 5 — Validation Layer: claimed missing DB fields/wiring"
# ============================================================================
check "hypothesis_supported / supporting_stats never written to any table" \
  "grep -n 'hypothesis_supported\|supporting_stats' *.py" \
  0
check "llm_counterargument field never generated or stored" \
  "grep -n 'llm_counterargument' *.py" \
  0
check "adversarial_critique / causal_inference / causal_discovery NOT wired to discovery engine" \
  "grep -n 'aiem_discovery_engine\|discovered_candidates\|trade_postmortems' adversarial_critique.py causal_inference.py causal_discovery.py" \
  0

# ============================================================================
section "MODULE 8 — Notifications: claimed _tg_send works everywhere EXCEPT discovery"
# ============================================================================
check "_tg_send is called 17+ times in main.py for other alert types" \
  "grep -n '_tg_send(' main.py" \
  1
echo ""
grep -c '_tg_send(' main.py 2>/dev/null | sed 's/^/  actual call count in main.py: /'

check "_tg_send has ZERO calls inside aiem_discovery_engine.py (the gap)" \
  "grep -n '_tg_send' aiem_discovery_engine.py" \
  0

# ============================================================================
section "CROSS-MODULE WIRING: Module 1 (GP) → Module 3 (stats bridge)"
# ============================================================================
check "signal_discovery_gp.py output format is TREE not the dict _evaluate() expects" \
  "grep -n 'def _evaluate\|return {' signal_discovery_gp.py" \
  1
check "No direct call from signal_discovery_gp.py into _evaluate()" \
  "grep -n '_evaluate(' signal_discovery_gp.py" \
  0

# ============================================================================
section "CROSS-MODULE WIRING: discovery_proposals reply-matching (approve/reject loop)"
# ============================================================================
check "discovery_proposals table referenced in getUpdates polling logic" \
  "grep -n 'discovery_proposals' *.py" \
  1
check "Regex token parser exists: ^(APPROVE|REJECT)\\s+([a-f0-9]{8})" \
  "grep -n 'APPROVE|REJECT' *.py" \
  1
check "Status transition pending -> approved/rejected exists in same file as parser" \
  "grep -n \"status.*=.*'approved'\|status.*=.*'rejected'\" *.py" \
  1

# ============================================================================
section "CROSS-MODULE WIRING: Module 4 (post-trade reflection) → Module 5 (validation)"
# ============================================================================
check "Post-trade reflection module exists at all (claimed FULLY ABSENT)" \
  "grep -rn 'post_trade_reflection\|trade_postmortems' . --include='*.py'" \
  0
check "ref_ token (refinement proposal) generation exists anywhere without Module 4 backing it" \
  "grep -n 'ref_\|refinement_proposal' *.py" \
  1

# ============================================================================
section "END-TO-END: DB-level proof (run against your actual database)"
# ============================================================================
echo ""
echo "Run these directly in psql to confirm claimed row-level behavior:"
cat <<'SQL'

  -- Confirm discovery_proposals table exists and has expected columns
  \d discovery_proposals

  -- Confirm no orphaned proposals (every approved/rejected has a responded_at)
  SELECT token, status, created_at, responded_at
  FROM discovery_proposals
  WHERE status IN ('approved','rejected') AND responded_at IS NULL;
  -- Expect: 0 rows. Any row here = status changed without a timestamp,
  -- meaning the "state machine" isn't actually enforced end-to-end.

  -- Confirm aiem_signal_discoveries rows have real p-values (not null/placeholder)
  SELECT id, hypothesis_text, p_value, oos_edge, status
  FROM aiem_signal_discoveries
  WHERE status = 'validated' AND (p_value IS NULL OR oos_edge IS NULL);
  -- Expect: 0 rows. Any row here = a "validated" signal shipped without stats.

  -- Confirm the "11 silent rejections" claim from the daily digest is real,
  -- not just printed text with nothing behind it
  SELECT COUNT(*) FROM aiem_signal_discoveries
  WHERE status = 'rejected' AND discovered_at::date = CURRENT_DATE;

SQL

# ============================================================================
section "SUMMARY"
# ============================================================================
echo ""
echo "Automated grep-based checks — PASS: $PASS   FAIL: $FAIL"
echo ""
echo "A FAIL above means either:"
echo "  (a) the agent's 'exists' claim was false (nothing actually found), OR"
echo "  (b) the agent's 'absent' claim was false (something WAS found)."
echo "Either way — do not let it proceed to build on top of that module until"
echo "you've read the RAW output yourself and confirmed which is true."
