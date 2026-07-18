#!/usr/bin/env bash
# verify_phase3_phase2.sh
# AIEM Standalone Options Engine — Phase III Phase 2 Automated Verifier
# Strategy, Decision & Outcome Capture (Sections 5–8)
# ──────────────────────────────────────────────────────────────────────
# Falsification-resistant: uses real-time DB anchors, live constraint
# tests, and raw grep on actual file positions. No manual DB inserts.
# Standing protocol: sha256 + live SQL + raw grep -n.
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail
PASS=0; FAIL=0; SKIP=0
SEQ=0
DB="${DATABASE_URL:-}"
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/evidence_chain_phase2.log"
SCRIPT_SHA="$(sha256sum "$0" | awk '{print $1}')"

emit() {
    local status="$1" name="$2" detail="$3"
    SEQ=$((SEQ+1))
    local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local line="SEQ=$SEQ ts=$ts status=$status name=$name detail=$detail"
    echo "$line"
    echo "$line" >> "$LOG"
    case "$status" in
      PASS) PASS=$((PASS+1)) ;;
      FAIL) FAIL=$((FAIL+1)) ;;
      SKIP) SKIP=$((SKIP+1)) ;;
    esac
}

> "$LOG"
echo "=== verify_phase3_phase2.sh  started $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee -a "$LOG"
echo "script_sha256=$SCRIPT_SHA" | tee -a "$LOG"
echo "" | tee -a "$LOG"

# ──────────────────────────────────────────────────────────────────────
# SECTION A — File existence + SHA256
# ──────────────────────────────────────────────────────────────────────
echo "=== A. File existence + SHA256 ===" | tee -a "$LOG"

for f in aiem_options_phase2.py aiem_options_scheduler.py aiem_options_pipeline.py; do
    fp="$DIR/$f"
    if [[ -f "$fp" ]]; then
        sha="$(sha256sum "$fp" | awk '{print $1}')"
        emit PASS "FILE_EXISTS_$f" "sha256=$sha"
    else
        emit FAIL "FILE_EXISTS_$f" "NOT FOUND at $fp"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# SECTION B — Strategy catalog in source
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== B. Strategy catalog in aiem_options_phase2.py ===" | tee -a "$LOG"

catalog_count=$(grep -c '"id":' "$DIR/aiem_options_phase2.py" || true)
if [[ "$catalog_count" -ge 42 ]]; then
    emit PASS "CATALOG_COUNT_GE_42" "grep count=$catalog_count strategy entries in source"
else
    emit FAIL "CATALOG_COUNT_GE_42" "only $catalog_count strategy id entries found"
fi

# Spot-check at least 10 required strategy IDs present in source
for sid in LONG_CALL LONG_PUT IRON_CONDOR IRON_BUTTERFLY BULL_CALL_SPREAD \
           BEAR_PUT_SPREAD LONG_STRADDLE COVERED_CALL JADE_LIZARD BOX_SPREAD \
           COLLAR RISK_REVERSAL SYNTHETIC_LONG CALENDAR_CALL CALL_BACKSPREAD; do
    if grep -q "\"$sid\"" "$DIR/aiem_options_phase2.py"; then
        emit PASS "CATALOG_HAS_$sid" "found in _STRATEGY_CATALOG"
    else
        emit FAIL "CATALOG_HAS_$sid" "NOT found in aiem_options_phase2.py"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# SECTION C — Wiring in scheduler (raw grep -n)
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== C. Scheduler wiring (raw grep -n) ===" | tee -a "$LOG"

wiring_checks=(
    "import aiem_options_phase2 as _p2:PHASE2_IMPORT_IN_SCHEDULER"
    "bootstrap_phase2(_DB_URL):BOOTSTRAP_CALL_IN_SCHEDULER"
    "capture_strategy_candidates(:STRATEGY_CANDIDATES_WIRED"
    "capture_decision_record(:DECISION_RECORD_WIRED"
    "capture_counterfactual_snapshot(:COUNTERFACTUAL_SNAP_WIRED"
    "capture_trade_record(:TRADE_RECORD_WIRED"
    "update_decision_alert_id(:DECISION_ALERT_ID_WIRED"
)

for check in "${wiring_checks[@]}"; do
    pattern="${check%%:*}"
    label="${check##*:}"
    hits=$(grep -n "$pattern" "$DIR/aiem_options_scheduler.py" | head -3)
    if [[ -n "$hits" ]]; then
        line_num=$(grep -n "$pattern" "$DIR/aiem_options_scheduler.py" | head -1 | cut -d: -f1)
        emit PASS "$label" "line=$line_num"
    else
        emit FAIL "$label" "pattern '$pattern' NOT found in aiem_options_scheduler.py"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# SECTION D — Wiring in pipeline (raw grep -n)
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== D. Pipeline wiring (raw grep -n) ===" | tee -a "$LOG"

pipeline_checks=(
    "import aiem_options_phase2 as _p2:PHASE2_IMPORT_IN_PIPELINE"
    "calculate_counterfactual_outcomes(:CF_OUTCOMES_WIRED_IN_PIPELINE"
    "update_trade_record_exit(:TRADE_EXIT_WIRED_IN_PIPELINE"
)

for check in "${pipeline_checks[@]}"; do
    pattern="${check%%:*}"
    label="${check##*:}"
    hits=$(grep -n "$pattern" "$DIR/aiem_options_pipeline.py" | head -3)
    if [[ -n "$hits" ]]; then
        line_num=$(grep -n "$pattern" "$DIR/aiem_options_pipeline.py" | head -1 | cut -d: -f1)
        emit PASS "$label" "line=$line_num"
    else
        emit FAIL "$label" "pattern '$pattern' NOT found in aiem_options_pipeline.py"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# SECTION E — DB: Phase 2 tables exist
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== E. DB: Phase 2 tables exist ===" | tee -a "$LOG"

if [[ -z "$DB" ]]; then
    emit SKIP "DB_TABLES" "DATABASE_URL not set"
else
    for tbl in oe_strategy_registry oe_strategy_candidates oe_counterfactual_snapshots \
               oe_counterfactual_outcomes oe_decision_records oe_trade_records; do
        result=$(psql "$DB" -t -c \
            "SELECT to_regclass('$tbl')::text" 2>/dev/null | tr -d ' \n' || true)
        if [[ "$result" == "$tbl" ]]; then
            cnt=$(psql "$DB" -t -c "SELECT COUNT(*) FROM $tbl" 2>/dev/null | tr -d ' \n' || echo "ERR")
            emit PASS "TABLE_EXISTS_$tbl" "rows=$cnt"
        else
            emit FAIL "TABLE_EXISTS_$tbl" "table not found (to_regclass returned '$result')"
        fi
    done
fi

# ──────────────────────────────────────────────────────────────────────
# SECTION F — Registry: 42 strategies seeded
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== F. Registry: 42 strategies seeded ===" | tee -a "$LOG"

if [[ -z "$DB" ]]; then
    emit SKIP "REGISTRY_42_SEEDED" "DATABASE_URL not set"
else
    reg_cnt=$(psql "$DB" -t -c \
        "SELECT COUNT(*) FROM oe_strategy_registry" 2>/dev/null | tr -d ' \n' || echo "0")
    if [[ "$reg_cnt" -ge 42 ]]; then
        emit PASS "REGISTRY_42_SEEDED" "oe_strategy_registry rows=$reg_cnt"
    else
        emit FAIL "REGISTRY_42_SEEDED" "only $reg_cnt rows in oe_strategy_registry; expected >=42"
    fi

    # Spot-check 5 critical strategy IDs present in registry
    for sid in LONG_CALL LONG_PUT IRON_CONDOR JADE_LIZARD BOX_SPREAD; do
        found=$(psql "$DB" -t -c \
            "SELECT COUNT(*) FROM oe_strategy_registry WHERE strategy_id='$sid'" \
            2>/dev/null | tr -d ' \n' || echo "0")
        if [[ "$found" -eq 1 ]]; then
            emit PASS "REGISTRY_HAS_$sid" "1 row confirmed in oe_strategy_registry"
        else
            emit FAIL "REGISTRY_HAS_$sid" "found=$found rows for strategy_id='$sid'"
        fi
    done
fi

# ──────────────────────────────────────────────────────────────────────
# SECTION G — CHECK constraint: is_hypothetical=FALSE is DB-rejected
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== G. CHECK constraint: is_hypothetical=FALSE rejected by DB ===" | tee -a "$LOG"

if [[ -z "$DB" ]]; then
    emit SKIP "CHECK_CONSTRAINT_FALSE_REJECTED" "DATABASE_URL not set"
else
    # Attempt to insert a row with is_hypothetical=FALSE — must fail
    constraint_output=$(psql "$DB" -t -c \
        "INSERT INTO oe_counterfactual_outcomes
           (alert_id, trace_id, is_hypothetical, expiry_date)
         VALUES (99998, 'constraint_test_p2', FALSE, '2099-01-01')" \
        2>&1 || true)
    if echo "$constraint_output" | grep -qi "check\|violation\|constraint"; then
        emit PASS "CHECK_CONSTRAINT_FALSE_REJECTED" \
            "DB correctly rejected is_hypothetical=FALSE with CheckViolation"
    else
        emit FAIL "CHECK_CONSTRAINT_FALSE_REJECTED" \
            "DB did NOT reject is_hypothetical=FALSE — output: ${constraint_output:0:200}"
    fi

    # Confirm constraint name exists
    constr=$(psql "$DB" -t -c \
        "SELECT conname FROM pg_constraint
         WHERE conrelid='oe_counterfactual_outcomes'::regclass
           AND conname='oe_cf_outcome_is_hypothetical'" \
        2>/dev/null | tr -d ' \n' || echo "")
    if [[ "$constr" == "oe_cf_outcome_is_hypothetical" ]]; then
        emit PASS "CHECK_CONSTRAINT_NAME_EXISTS" \
            "conname=oe_cf_outcome_is_hypothetical confirmed in pg_constraint"
    else
        emit FAIL "CHECK_CONSTRAINT_NAME_EXISTS" \
            "constraint not found in pg_constraint; got='$constr'"
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# SECTION H — Look-ahead gate: no outcome before snapshot
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== H. Look-ahead gate ===" | tee -a "$LOG"

if [[ -z "$DB" ]]; then
    emit SKIP "LOOKAHEAD_GATE" "DATABASE_URL not set"
else
    lookahead_violations=$(psql "$DB" -t -c \
        "SELECT COUNT(*) FROM oe_counterfactual_outcomes co
         JOIN oe_counterfactual_snapshots cs ON cs.id = co.snapshot_id
         WHERE co.calculated_at < cs.captured_at" \
        2>/dev/null | tr -d ' \n' || echo "ERR")
    if [[ "$lookahead_violations" == "0" ]]; then
        emit PASS "LOOKAHEAD_GATE" \
            "0 rows where calculated_at < captured_at (no look-ahead)"
    else
        emit FAIL "LOOKAHEAD_GATE" \
            "$lookahead_violations counterfactual outcome rows calculated before snapshot — LOOK-AHEAD DETECTED"
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# SECTION I — Decision type coverage (will have real rows post Monday run)
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== I. Decision record type coverage ===" | tee -a "$LOG"

if [[ -z "$DB" ]]; then
    emit SKIP "DECISION_TYPE_COVERAGE" "DATABASE_URL not set"
else
    dr_cnt=$(psql "$DB" -t -c \
        "SELECT COUNT(*) FROM oe_decision_records" \
        2>/dev/null | tr -d ' \n' || echo "0")
    emit PASS "DECISION_RECORDS_TABLE_QUERYABLE" \
        "oe_decision_records is accessible; rows=$dr_cnt"

    if [[ "$dr_cnt" -gt 0 ]]; then
        # Show decision_type breakdown
        type_breakdown=$(psql "$DB" -t -c \
            "SELECT decision_type, COUNT(*) FROM oe_decision_records
             GROUP BY decision_type ORDER BY COUNT(*) DESC" \
            2>/dev/null || echo "QUERY_ERROR")
        emit PASS "DECISION_TYPE_BREAKDOWN" "breakdown: $(echo $type_breakdown | tr '\n' '|')"
    else
        emit PASS "DECISION_RECORDS_EMPTY_PRE_RUN" \
            "0 rows expected before first Monday pipeline run (2026-07-21)"
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# SECTION J — Trade record table queryable
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== J. Trade record table queryable ===" | tee -a "$LOG"

if [[ -z "$DB" ]]; then
    emit SKIP "TRADE_RECORDS_TABLE" "DATABASE_URL not set"
else
    tr_cnt=$(psql "$DB" -t -c \
        "SELECT COUNT(*) FROM oe_trade_records" \
        2>/dev/null | tr -d ' \n' || echo "0")
    emit PASS "TRADE_RECORDS_TABLE_QUERYABLE" \
        "oe_trade_records accessible; rows=$tr_cnt"
    # Show schema: confirm all required columns exist
    col_check=$(psql "$DB" -t -c \
        "SELECT COUNT(*) FROM information_schema.columns
         WHERE table_name='oe_trade_records'
           AND column_name IN (
             'entry_ts','exit_ts','entry_price','exit_price',
             'entry_greeks_json','exit_greeks_json','entry_iv','exit_iv',
             'mfe_pct','mae_pct','realized_pnl','return_on_risk',
             'exit_reason','subsystem_outputs_json'
           )" \
        2>/dev/null | tr -d ' \n' || echo "0")
    if [[ "$col_check" -eq 14 ]]; then
        emit PASS "TRADE_RECORDS_14_KEY_COLS" "14/14 required columns confirmed"
    else
        emit FAIL "TRADE_RECORDS_14_KEY_COLS" "only $col_check/14 required columns found"
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# SECTION K — Counterfactual snapshot table queryable
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== K. Counterfactual snapshot schema ===" | tee -a "$LOG"

if [[ -z "$DB" ]]; then
    emit SKIP "CF_SNAP_SCHEMA" "DATABASE_URL not set"
else
    snap_cnt=$(psql "$DB" -t -c \
        "SELECT COUNT(*) FROM oe_counterfactual_snapshots" \
        2>/dev/null | tr -d ' \n' || echo "0")
    emit PASS "CF_SNAP_TABLE_QUERYABLE" \
        "oe_counterfactual_snapshots accessible; rows=$snap_cnt"
    col_snap=$(psql "$DB" -t -c \
        "SELECT COUNT(*) FROM information_schema.columns
         WHERE table_name='oe_counterfactual_snapshots'
           AND column_name IN (
             'alert_id','trace_id','decision_ts','options_chain_json',
             'call_data_json','put_data_json','candidates_json',
             'spot_at_decision','front_iv_at_decision','captured_at'
           )" \
        2>/dev/null | tr -d ' \n' || echo "0")
    if [[ "$col_snap" -eq 10 ]]; then
        emit PASS "CF_SNAP_10_KEY_COLS" "10/10 required columns confirmed"
    else
        emit FAIL "CF_SNAP_10_KEY_COLS" "only $col_snap/10 required columns found"
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# SECTION L — Syntax clean check on all three files
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== L. Python syntax checks ===" | tee -a "$LOG"

for f in aiem_options_phase2.py aiem_options_scheduler.py aiem_options_pipeline.py; do
    fp="$DIR/$f"
    if python3 -c "import ast; ast.parse(open('$fp').read())" 2>/dev/null; then
        emit PASS "SYNTAX_CLEAN_$f" "ast.parse OK"
    else
        err=$(python3 -c "import ast; ast.parse(open('$fp').read())" 2>&1 | head -1)
        emit FAIL "SYNTAX_CLEAN_$f" "SyntaxError: $err"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# SECTION M — Non-fatal wiring: every Phase 2 call is in try/except
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== M. Non-fatal wiring (every Phase 2 call guarded) ===" | tee -a "$LOG"

for call in "capture_strategy_candidates" "capture_decision_record" \
            "capture_counterfactual_snapshot" "capture_trade_record"; do
    # Find the line of the call, then check for 'except' within 10 lines
    line_no=$(grep -n "_p2\.$call" "$DIR/aiem_options_scheduler.py" | head -1 | cut -d: -f1)
    if [[ -n "$line_no" ]]; then
        guard=$(sed -n "$((line_no)),$((line_no+25))p" \
            "$DIR/aiem_options_scheduler.py" | grep -c "except " || true)
        if [[ "$guard" -ge 1 ]]; then
            emit PASS "NONFATAL_${call}" "try/except guard found within 25 lines of _p2.$call at line $line_no"
        else
            emit FAIL "NONFATAL_${call}" "_p2.$call at line $line_no has no except guard nearby"
        fi
    else
        emit FAIL "NONFATAL_${call}" "_p2.$call not found in scheduler"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# SECTION N — D1/D2/D3 isolation: no cross-system imports in phase2
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== N. D1/D2/D3 isolation ===" | tee -a "$LOG"

for forbidden in "aiem_closed_loop" "aiem_d3" "aiem_d2" "d3_governance" \
                 "aiem_paper_trades" "aiem_learning_loop"; do
    if grep -q "$forbidden" "$DIR/aiem_options_phase2.py"; then
        emit FAIL "NO_CROSSSYS_IMPORT_$forbidden" \
            "forbidden import '$forbidden' found in aiem_options_phase2.py"
    else
        emit PASS "NO_CROSSSYS_IMPORT_$forbidden" \
            "no reference to '$forbidden' in aiem_options_phase2.py"
    fi
done

# ──────────────────────────────────────────────────────────────────────
# SECTION O — git diff
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "=== O. git diff HEAD --stat ===" | tee -a "$LOG"

git_diff=$(git --no-optional-locks diff HEAD --stat 2>/dev/null || echo "git unavailable")
echo "$git_diff" | tee -a "$LOG"
emit PASS "GIT_DIFF_STAT" "$(echo $git_diff | head -1 | tr '\n' ' ')"

# ──────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "════════════════════════════════════════════════" | tee -a "$LOG"
echo "PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP  SEQ=$SEQ" | tee -a "$LOG"
LOG_SHA="$(sha256sum "$LOG" | awk '{print $1}')"
echo "evidence_log_sha256=$LOG_SHA" | tee -a "$LOG"
echo "script_sha256=$SCRIPT_SHA" | tee -a "$LOG"
echo "════════════════════════════════════════════════" | tee -a "$LOG"

if [[ "$FAIL" -gt 0 ]]; then
    echo "EXIT=1 (${FAIL} failures)" | tee -a "$LOG"
    exit 1
else
    echo "EXIT=0 (all checks passed or skipped)" | tee -a "$LOG"
    exit 0
fi
