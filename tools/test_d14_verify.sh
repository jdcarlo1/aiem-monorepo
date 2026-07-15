#!/usr/bin/env bash
# tools/test_d14_verify.sh — D14 Verification Negative-Test Suite
#
# Tests:
#   1. Missing D14_DEBATE_POST  → verifier must detect FAIL
#   2. Invalid SHA-256 chain    → verifier must detect chain_invalid
#   3. Valid full triplet       → verifier must PASS
#   4. Missing ALL events       → verifier must detect all 3 missing proofs
#
# Outputs raw grep proof, exact file/line references, SHA-256 hashes of
# modified files, and test results.
#
# Usage: bash tools/test_d14_verify.sh
set -euo pipefail
cd /home/runner/workspace

PY=python3
CAPTURE_LOG=".local/d14_live_capture.log"
PASS=0; FAIL=0
TODAY=$($PY -c "import datetime; print(datetime.date.today())")

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

run_test() {
    local name="$1"; shift
    echo -n "  [$name] ... "
    if "$@"; then
        echo "PASS"
        PASS=$((PASS+1))
    else
        echo "FAIL  ← !!!"
        FAIL=$((FAIL+1))
    fi
}

# ── Backup & restore helpers ─────────────────────────────────────────────────
backup_capture() { cp "$CAPTURE_LOG" "/tmp/d14_capture_backup.json" 2>/dev/null || true; }
restore_capture() { cp "/tmp/d14_capture_backup.json" "$CAPTURE_LOG" 2>/dev/null || true; }

# ── SECTION 0: GREP PROOF ────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "  SECTION 0 — GREP: watchdog checks all 4 requirements"
echo "========================================================"
echo ""

echo "--- aiem_d14_verifier.py: 4 required proof labels ---"
grep -n "D14_LAYER9_READ\|D14_DEBATE_PRE\|D14_DEBATE_POST\|chain_valid\|verify_chain" \
  artifacts/stock-scanner-api/aiem_d14_verifier.py | head -30
echo ""

echo "--- main.py: SHA-256 chain injection (L9+PRE block) ---"
grep -n "_d14_chain_hash_after_pre\|d14_chain:\|sha256.*d14\|D14_LAYER9.*chain\|_d14_hl\." \
  artifacts/stock-scanner-api/main.py | head -20
echo ""

echo "--- main.py: POST chain linkage ---"
grep -n "_d14_chain_hash_after_pre\|_d14_hl2\." \
  artifacts/stock-scanner-api/main.py | head -10
echo ""

echo "--- main.py: _log_finish D14 verify trigger ---"
grep -n "D14_VERIFY\|_aiem_d14_run_verification_async\|d14_verify_fn" \
  artifacts/stock-scanner-api/main.py | head -20
echo ""

echo "--- aiem_paper_recovery.py: d14_verify_fn wiring ---"
grep -n "d14_verify_fn" \
  artifacts/stock-scanner-api/aiem_paper_recovery.py | head -10
echo ""

echo "--- File/line proof for retry function ---"
grep -n "def _aiem_d14_retry_debate_only\|def _aiem_d14_run_verification_async" \
  artifacts/stock-scanner-api/main.py
echo ""

# ── SECTION 1: SYNTAX CHECK ──────────────────────────────────────────────────
echo "========================================================"
echo "  SECTION 1 — SYNTAX CHECK"
echo "========================================================"
echo ""

syntax_check() {
    local file="$1"
    if $PY -m py_compile "$file" 2>&1; then
        echo "  SYNTAX OK: $file"
        return 0
    else
        echo "  SYNTAX ERROR: $file"
        return 1
    fi
}

run_test "syntax:aiem_d14_verifier"  syntax_check artifacts/stock-scanner-api/aiem_d14_verifier.py
run_test "syntax:aiem_paper_recovery" syntax_check artifacts/stock-scanner-api/aiem_paper_recovery.py
# main.py is too large for py_compile in a test script, use ast.parse on the D14 sections
run_test "syntax:main.py_ast" bash -c "
  $PY -c \"
import ast, sys
with open('artifacts/stock-scanner-api/main.py') as f:
    src = f.read()
try:
    ast.parse(src)
    print('  AST parse OK: main.py')
    sys.exit(0)
except SyntaxError as e:
    print(f'  SYNTAX ERROR in main.py: {e}')
    sys.exit(1)
\"
"
echo ""

# ── SECTION 2: SHA-256 HASHES OF MODIFIED FILES ──────────────────────────────
echo "========================================================"
echo "  SECTION 2 — SHA-256 HASHES OF MODIFIED FILES"
echo "========================================================"
echo ""
sha256sum \
  artifacts/stock-scanner-api/aiem_d14_verifier.py \
  artifacts/stock-scanner-api/aiem_paper_recovery.py \
  artifacts/stock-scanner-api/main.py 2>/dev/null || \
  $PY -c "
import hashlib, sys
files = [
  'artifacts/stock-scanner-api/aiem_d14_verifier.py',
  'artifacts/stock-scanner-api/aiem_paper_recovery.py',
  'artifacts/stock-scanner-api/main.py',
]
for f in files:
    try:
        h = hashlib.sha256(open(f,'rb').read()).hexdigest()
        print(f'  {h}  {f}')
    except Exception as e:
        print(f'  ERROR: {f}: {e}')
"
echo ""

# ── SECTION 3: NEGATIVE TESTS ────────────────────────────────────────────────
echo "========================================================"
echo "  SECTION 3 — NEGATIVE TESTS"
echo "========================================================"
echo ""

# ── T1: Missing D14_DEBATE_POST ──────────────────────────────────────────────
test_missing_post() {
    backup_capture
    # Append fake run: only LAYER9 + PRE (no POST)
    $PY - <<PYEOF
import json, sys
TODAY = "${TODAY}"
events = [
    {"event":"D14_LAYER9","ts":TODAY+"T00:00:01Z",
     "trigger_source":"neg_missing_post","ticker":"FAKEPOST",
     "trace_id":"trace-neg-missing","candidate_id":"cand-neg-1",
     "layer9_score":30.0,"vpin_raw":0.1,"hurst_raw":0.6,
     "garch_vote":0,"garch_reason":"neg test"},
    {"event":"D14_DEBATE_PRE","ts":TODAY+"T00:00:02Z",
     "trigger_source":"neg_missing_post","ticker":"FAKEPOST",
     "trace_id":"trace-neg-missing","candidate_id":"cand-neg-1",
     "signal_context_d14_keys":{"vpin_raw":0.1,"vpin_score":0.0,
       "hurst_raw":0.6,"hurst_score":0.0,"garch_vote":0,
       "garch_reason":"neg test","layer9_regime":"trending",
       "layer9_score":30.0}},
    # D14_DEBATE_POST deliberately omitted
]
with open("${CAPTURE_LOG}","a") as f:
    for ev in events:
        f.write(json.dumps(ev)+"\n")
print("  wrote neg_missing_post events (no POST)")
PYEOF

    local result
    result=$($PY -c "
import sys, datetime
sys.path.insert(0,'artifacts/stock-scanner-api')
import aiem_d14_verifier as v
today = datetime.date.today()
r = v.verify_d14_proofs(today, 'neg_missing_post')
print(f'  pass={r[\"pass\"]} missing={r[\"missing_proofs\"]} events={r[\"events_count\"]}')
ok = (not r['pass'] and
      'D14_DEBATE_POST' in r['missing_proofs'])
sys.exit(0 if ok else 1)
" 2>&1)
    local rc=$?
    echo "$result"
    restore_capture
    return $rc
}

# ── T2: Invalid SHA-256 chain ────────────────────────────────────────────────
test_chain_tamper() {
    backup_capture
    $PY - <<PYEOF
import json, hashlib, sys
TODAY = "${TODAY}"

def chain_hash(ev, prev):
    clean = {k:v for k,v in ev.items() if k not in ("sha256","prev_hash")}
    canon = hashlib.sha256(json.dumps(clean,sort_keys=True).encode()).hexdigest()
    return hashlib.sha256((canon+prev).encode()).hexdigest()

trace_id = "trace-chain-tamper"
seed = hashlib.sha256(f"d14_chain:{trace_id}:{TODAY}".encode()).hexdigest()

ev_l9 = {"event":"D14_LAYER9","ts":TODAY+"T00:01:00Z",
         "trigger_source":"neg_chain_tamper","ticker":"CHAINTST",
         "trace_id":trace_id,"candidate_id":"cand-chain-1",
         "layer9_score":40.0,"vpin_raw":0.05,"hurst_raw":0.75,
         "garch_vote":0,"garch_reason":"chain test"}
ev_pre = {"event":"D14_DEBATE_PRE","ts":TODAY+"T00:01:01Z",
          "trigger_source":"neg_chain_tamper","ticker":"CHAINTST",
          "trace_id":trace_id,"candidate_id":"cand-chain-1",
          "signal_context_d14_keys":{"vpin_raw":0.05,"vpin_score":0.0,
            "hurst_raw":0.75,"hurst_score":0.0,"garch_vote":0,
            "garch_reason":"chain test","layer9_regime":"trending",
            "layer9_score":40.0}}
ev_post = {"event":"D14_DEBATE_POST","ts":TODAY+"T00:01:02Z",
           "trigger_source":"neg_chain_tamper","ticker":"CHAINTST",
           "trace_id":trace_id,"candidate_id":"cand-chain-1",
           "verdict":"BUY","d14_tier1_activation":{"vpin_in_bull":True,
           "hurst_in_bull":True,"garch_in_bull":False,"vpin_in_bear":False,
           "hurst_in_bear":False,"garch_in_bear":False}}

ev_l9["prev_hash"]  = seed
ev_l9["sha256"]     = chain_hash(ev_l9, seed)
c1 = ev_l9["sha256"]
ev_pre["prev_hash"] = c1
ev_pre["sha256"]    = chain_hash(ev_pre, c1)
c2 = ev_pre["sha256"]
ev_post["prev_hash"] = c2
ev_post["sha256"]    = chain_hash(ev_post, c2)

# TAMPER: mutate layer9_score AFTER hashing — chain now stale
ev_l9["layer9_score"] = 99.9

with open("${CAPTURE_LOG}","a") as f:
    f.write(json.dumps(ev_l9)+"\n")
    f.write(json.dumps(ev_pre)+"\n")
    f.write(json.dumps(ev_post)+"\n")
print("  wrote neg_chain_tamper events (layer9_score mutated after hashing)")
PYEOF

    local result
    result=$($PY -c "
import sys, datetime
sys.path.insert(0,'artifacts/stock-scanner-api')
import aiem_d14_verifier as v
today = datetime.date.today()
r = v.verify_d14_proofs(today, 'neg_chain_tamper')
print(f'  pass={r[\"pass\"]} chain_valid={r[\"chain_valid\"]} chain_error={r[\"chain_error\"]}')
ok = (not r['pass'] and
      not r['chain_valid'] and
      ('mismatch' in r['chain_error'] or 'hash' in r['chain_error'].lower() or 'invalid' in r['chain_error'].lower()))
sys.exit(0 if ok else 1)
" 2>&1)
    local rc=$?
    echo "$result"
    restore_capture
    return $rc
}

# ── T3: Missing ALL events ───────────────────────────────────────────────────
test_all_missing() {
    # Use a trigger source that has never been used — no events will be found
    local result
    result=$($PY -c "
import sys, datetime
sys.path.insert(0,'artifacts/stock-scanner-api')
import aiem_d14_verifier as v
today = datetime.date.today()
r = v.verify_d14_proofs(today, 'trigger_that_never_ran')
print(f'  pass={r[\"pass\"]} missing={r[\"missing_proofs\"]} events={r[\"events_count\"]}')
ok = (not r['pass'] and
      set(r['missing_proofs']) >= {'D14_LAYER9_READ','D14_DEBATE_PRE','D14_DEBATE_POST'} and
      r['events_count'] == 0)
sys.exit(0 if ok else 1)
" 2>&1)
    local rc=$?
    echo "$result"
    return $rc
}

# ── T4: Valid run with correct chain ─────────────────────────────────────────
test_valid_chain() {
    backup_capture
    $PY - <<PYEOF
import json, hashlib, sys
TODAY = "${TODAY}"

def chain_hash(ev, prev):
    clean = {k:v for k,v in ev.items() if k not in ("sha256","prev_hash")}
    canon = hashlib.sha256(json.dumps(clean,sort_keys=True).encode()).hexdigest()
    return hashlib.sha256((canon+prev).encode()).hexdigest()

trace_id = "trace-valid-chain-ok"
seed = hashlib.sha256(f"d14_chain:{trace_id}:{TODAY}".encode()).hexdigest()

ev_l9 = {"event":"D14_LAYER9","ts":TODAY+"T00:02:00Z",
         "trigger_source":"pos_valid_chain","ticker":"VALIDCHN",
         "trace_id":trace_id,"candidate_id":"cand-valid-1",
         "layer9_score":50.0,"vpin_raw":0.08,"hurst_raw":0.72,
         "garch_vote":0,"garch_reason":"positive test"}
ev_pre = {"event":"D14_DEBATE_PRE","ts":TODAY+"T00:02:01Z",
          "trigger_source":"pos_valid_chain","ticker":"VALIDCHN",
          "trace_id":trace_id,"candidate_id":"cand-valid-1",
          "signal_context_d14_keys":{"vpin_raw":0.08,"vpin_score":0.0,
            "hurst_raw":0.72,"hurst_score":0.0,"garch_vote":0,
            "garch_reason":"positive test","layer9_regime":"trending",
            "layer9_score":50.0}}
ev_post = {"event":"D14_DEBATE_POST","ts":TODAY+"T00:02:02Z",
           "trigger_source":"pos_valid_chain","ticker":"VALIDCHN",
           "trace_id":trace_id,"candidate_id":"cand-valid-1",
           "verdict":"BUY","d14_tier1_activation":{"vpin_in_bull":True,
           "hurst_in_bull":True,"garch_in_bull":False,"vpin_in_bear":False,
           "hurst_in_bear":False,"garch_in_bear":False}}

ev_l9["prev_hash"]   = seed
ev_l9["sha256"]      = chain_hash(ev_l9, seed)
c1 = ev_l9["sha256"]
ev_pre["prev_hash"]  = c1
ev_pre["sha256"]     = chain_hash(ev_pre, c1)
c2 = ev_pre["sha256"]
ev_post["prev_hash"] = c2
ev_post["sha256"]    = chain_hash(ev_post, c2)

with open("${CAPTURE_LOG}","a") as f:
    f.write(json.dumps(ev_l9)+"\n")
    f.write(json.dumps(ev_pre)+"\n")
    f.write(json.dumps(ev_post)+"\n")
print("  wrote pos_valid_chain events (correct chain)")
PYEOF

    local result
    result=$($PY -c "
import sys, datetime
sys.path.insert(0,'artifacts/stock-scanner-api')
import aiem_d14_verifier as v
today = datetime.date.today()
r = v.verify_d14_proofs(today, 'pos_valid_chain')
print(f'  pass={r[\"pass\"]} chain_valid={r[\"chain_valid\"]} tickers_ok={r[\"tickers_ok\"]}')
ok = (r['pass'] and r['chain_valid'] and 'VALIDCHN' in r['tickers_ok'])
sys.exit(0 if ok else 1)
" 2>&1)
    local rc=$?
    echo "$result"
    restore_capture
    return $rc
}

# ── Run all negative tests ───────────────────────────────────────────────────
echo "--- T1: Missing D14_DEBATE_POST ---"
run_test "T1:missing_debate_post"  test_missing_post
echo ""

echo "--- T2: Invalid SHA-256 chain (field tampered after hashing) ---"
run_test "T2:invalid_chain_hash"   test_chain_tamper
echo ""

echo "--- T3: Missing ALL D14 events ---"
run_test "T3:all_events_missing"   test_all_missing
echo ""

echo "--- T4: Valid full triplet + correct chain (should PASS) ---"
run_test "T4:valid_chain_pass"     test_valid_chain
echo ""

# ── SECTION 4: FINAL SUMMARY ─────────────────────────────────────────────────
echo "========================================================"
echo "  SECTION 4 — FINAL SUMMARY"
echo "========================================================"
echo ""
echo "  Tests run:  $((PASS+FAIL))"
echo "  PASS:       $PASS"
echo "  FAIL:       $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "OVERALL: *** FAIL *** ($FAIL test(s) failed)"
    exit 1
else
    echo "OVERALL: PASS — all D14 verification negative tests confirmed"
    exit 0
fi
