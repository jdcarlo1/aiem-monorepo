#!/usr/bin/env bash
# raw_evidence_phase3p1.sh
# Raw evidence for Phase III Phase 1 — no PASS/FAIL labels, no reformatting.
# All grep output is grep -n (line numbers + matched text).
# SQL shown verbatim + full result set.
# Python failure tests let exceptions propagate uncaught (full traceback to stderr).
# set +e intentional — failure tests exit non-zero; we capture all output anyway.
set +e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCHED="$ROOT/artifacts/stock-scanner-api/aiem_options_scheduler.py"
REG="$ROOT/artifacts/stock-scanner-api/aiem_options_registries.py"
PIPE="$ROOT/artifacts/stock-scanner-api/aiem_options_pipeline.py"
PYDIR="$ROOT/artifacts/stock-scanner-api"

sep() {
    echo ""
    echo "════════════════════════════════════════════════════════════════════════════"
    echo "## $1"
    echo "════════════════════════════════════════════════════════════════════════════"
}

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 6 — sha256sum of tools/verified_run.sh and verify_chain.sh"
# ══════════════════════════════════════════════════════════════════════════════
echo "--- tools/verified_run.sh ---"
sha256sum "$ROOT/artifacts/stock-scanner-api/tools/verified_run.sh"
echo "--- verify_chain.sh ---"
sha256sum "$ROOT/artifacts/stock-scanner-api/verify_chain.sh"
echo ""
echo "Canonical expected:"
echo "  verified_run.sh = 8146a523cdc7fcecdf26451789f6792db8a7091bb0669f07a9c2caf4670119f4"
echo "  verify_chain.sh = ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f"

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 2 — Full sha256sum output for the three files"
# ══════════════════════════════════════════════════════════════════════════════
sha256sum "$SCHED" "$REG" "$PIPE"

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 7 — git diff HEAD --stat"
# ══════════════════════════════════════════════════════════════════════════════
git --no-optional-locks -C "$ROOT" diff HEAD --stat

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 1 / SECTION 2 — Raw grep -n: every subsystem indicator ID (aiem_options_scheduler.py)"
# ══════════════════════════════════════════════════════════════════════════════

for pattern in \
    "POLY_CLOSE_PRICE" "POLY_VWAP" "POLY_CLOSE_STRENGTH" \
    "OSS_FRONT_IV" "OSS_GEX_REGIME" "OSS_PC_SKEW_PP" "OSS_TERM_RATIO" "OSS_BACK_IV" \
    "TECH_STOCK_DIRECTION" "TECH_IV_CRUSH_RISK" \
    "MKT_REGIME_TAG" "MKT_GEX_REGIME" "MKT_TERM_STRUCTURE" \
    "VOLREG_FRONT_IV_CLASS" \
    "PM_CONFIDENCE" "PM_GAP_PCT" \
    "INTRA_PM_HIGH_BROKEN" "INTRA_PM_LOW_HELD" \
    "MTF_ALIGNMENT_SCORE" "MTF_CONFLICT_SCORE" "MTF_DOMINANT_BIAS" \
    "MTF_ENTRY_TIMING" "MTF_BULLISH_TF_COUNT" \
    "OC_CONTRACTS_TOTAL" "OC_STRATEGIES_COUNT" "OC_BEST_STRATEGY" "OC_CHAIN_CALLS_CNT" \
    "EI_STRATEGIES_TOTAL" "EI_STRATEGIES_APPROVED" "EI_BEST_FILL_PROB" "EI_BEST_NET_EDGE" \
    "OPT_EXPECTED_MOVE" "OPT_OI_BELOW_SPOT" "OPT_BEARISH_SIGNAL_COUNT" \
    "BS_CALL_GAMMA" "BS_PUT_VEGA" \
    "SIZE_CALL_PREMIUM_AT_RISK" "SIZE_CALL_SLIPPAGE" \
    "capture_options_metrics" "enrich_metrics_oss" \
    "REQ6_PUT_SCORE" "REQ6_MARGIN" \
    "DECISION_DIRECTION" "DECISION_MARGIN" \
    "VERIFY_ALERT_ID" "VERIFY_CHAIN_SHA" "VERIFY_ELAPSED_S"; do
    echo "--- grep -n \"$pattern\" ---"
    grep -n "$pattern" "$SCHED"
    echo ""
done

# Quoted variants (exact indicator ID, not substrings)
for pattern in \
    '"PM_SCORE"' '"PAT_SCORE"' '"PAT_COUNT"' '"PAT_BULLISH"' '"PAT_BEARISH"' \
    '"CCS_SCORE"' '"CCS_STRATEGY_POP"' '"CCS_STRATEGY_EV"' \
    '"OPT_IV_RANK"' '"VOLREG_VRP"' '"VOLREG_IV_RANK"' \
    '"BS_CALL_DELTA"' '"BS_PUT_DELTA"' '"BS_CALL_POP"' '"BS_PUT_POP"' \
    '"REQ6_CALL_SCORE"' '"REQ6_CALL_"'; do
    echo "--- grep -n $pattern ---"
    grep -n "$pattern" "$SCHED"
    echo ""
done

echo "--- grep -n \"_rc_pat(_p_cid\" [individual pattern snap] ---"
grep -n "_rc_pat(_p_cid" "$SCHED"
echo ""
echo "--- grep -n \"update_metrics_alert_id\" [Stage 8] ---"
grep -n "update_metrics_alert_id" "$SCHED"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 1 / SECTION 3 — Raw grep -n: registry function surface (aiem_options_registries.py)"
# ══════════════════════════════════════════════════════════════════════════════
for fn in \
    "def register_indicator" "def snap_indicator" \
    "def register_pattern"   "def snap_pattern" \
    "def capture_options_metrics" "def enrich_metrics_oss" \
    "def update_metrics_alert_id" "def update_metrics_outcome_by_alert" \
    "def update_metrics_outcome" \
    "def assert_no_missing_indicators" \
    "def assert_pattern_scan_complete" \
    "def assert_data_freshness" \
    "def bootstrap_registries" \
    "class RegistryValidationError"; do
    echo "--- grep -n \"$fn\" ---"
    grep -n "$fn" "$REG"
    echo ""
done

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 1 / SECTION 4 — Raw grep -n: pipeline outcome wiring (aiem_options_pipeline.py)"
# ══════════════════════════════════════════════════════════════════════════════
echo "--- grep -n \"import aiem_options_registries\" ---"
grep -n "import aiem_options_registries" "$PIPE"
echo ""
echo "--- grep -n \"update_metrics_outcome_by_alert\" ---"
grep -n "update_metrics_outcome_by_alert" "$PIPE"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 1 / SECTION 5 — Raw grep -n: registry helper infrastructure (aiem_options_scheduler.py)"
# ══════════════════════════════════════════════════════════════════════════════
echo "--- grep -n \"_reg_ready\" ---"
grep -n "_reg_ready" "$SCHED"
echo ""
echo "--- grep -n \"def _rc(family\" ---"
grep -n "def _rc(family" "$SCHED"
echo ""
echo "--- grep -n \"def _rc_pat(\" ---"
grep -n "def _rc_pat(" "$SCHED"
echo ""
echo "--- grep -n \"bootstrap_registries(_DB_URL)\" ---"
grep -n "bootstrap_registries(_DB_URL)" "$SCHED"
echo ""
echo "--- grep -n \"def _rc\(\*a\" [non-fatal fallback] ---"
grep -n "def _rc(\*a" "$SCHED"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 1 / SECTION 6 — Raw grep -n: failure gate injection path (aiem_options_scheduler.py)"
# ══════════════════════════════════════════════════════════════════════════════
echo "--- grep -n \"assert_no_missing_indicators\" ---"
grep -n "assert_no_missing_indicators" "$SCHED"
echo ""
echo "--- grep -n \"assert_pattern_scan_complete\" ---"
grep -n "assert_pattern_scan_complete" "$SCHED"
echo ""
echo "--- grep -n \"assert_data_freshness\" ---"
grep -n "assert_data_freshness" "$SCHED"
echo ""
echo "--- grep -n \"gate_failures\" ---"
grep -n "gate_failures" "$SCHED"
echo ""
echo "--- grep -n 'verify_result[\"ready_for_decision\"] = False' ---"
grep -n 'verify_result\["ready_for_decision"\] = False' "$SCHED"
echo ""
echo "--- grep -n \"REGISTRY VALIDATION BLOCKED PIPELINE\" ---"
grep -n "REGISTRY VALIDATION BLOCKED PIPELINE" "$SCHED"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 3 — Raw SQL query + full result set (table existence)"
# ══════════════════════════════════════════════════════════════════════════════
cat <<'SQL'
-- Query executed:
SELECT
    t.table_name,
    pg_size_pretty(pg_total_relation_size(quote_ident(t.table_name))) AS total_size,
    (SELECT count(*) FROM information_schema.columns c
     WHERE c.table_schema = 'public' AND c.table_name = t.table_name) AS col_count
FROM information_schema.tables t
WHERE t.table_schema = 'public'
  AND t.table_name LIKE 'oe_%'
ORDER BY t.table_name;

-- Column detail for oe_options_metrics:
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'oe_options_metrics'
ORDER BY ordinal_position;
SQL

echo ""
echo "-- Result set: --"
python3 -c "
import psycopg2, os, sys
url = os.environ.get('DATABASE_URL','')
if not url:
    print('ERROR: DATABASE_URL not set')
    sys.exit(1)
conn = psycopg2.connect(url, connect_timeout=5)
cur = conn.cursor()
cur.execute('''
    SELECT t.table_name,
           pg_size_pretty(pg_total_relation_size(quote_ident(t.table_name))) AS total_size,
           (SELECT count(*) FROM information_schema.columns c
            WHERE c.table_schema = %s AND c.table_name = t.table_name) AS col_count
    FROM information_schema.tables t
    WHERE t.table_schema = %s AND t.table_name LIKE %s
    ORDER BY t.table_name
''', ('public','public','oe_%'))
rows = cur.fetchall()
print(f\"{'table_name':<38} {'total_size':>12} {'col_count':>10}\")
print('-'*63)
for r in rows:
    print(f\"{r[0]:<38} {r[1]:>12} {r[2]:>10}\")
print(f'\n({len(rows)} rows)')

cur2 = conn.cursor()
cur2.execute('''
    SELECT column_name, data_type, is_nullable, ordinal_position
    FROM information_schema.columns
    WHERE table_schema = %s AND table_name = %s
    ORDER BY ordinal_position
''', ('public','oe_options_metrics'))
cols = cur2.fetchall()
print('\noe_options_metrics columns:')
print(f\"  {'#':>3}  {'column_name':<35} {'data_type':<30} nullable\")
print('  ' + '-'*78)
for c in cols:
    print(f\"  {c[3]:>3}  {c[0]:<35} {c[1]:<30} {c[2]}\")
conn.close()
" 2>&1

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 4 / SECTION 8 — Full uncaught traceback: assert_no_missing_indicators"
# ══════════════════════════════════════════════════════════════════════════════
echo "python3 (uncaught — no try/except; exception propagates to top level)"
echo "cwd: $PYDIR"
echo ""
cd "$PYDIR"
python3 - <<'PYEOF' 2>&1
import sys
sys.path.insert(0, '.')
import aiem_options_registries as reg

FAKE_TRACE = "VERIFY_FAKE_TRACE_PHASE3_P1_UNCAUGHT_000"
REQUIRED   = ["POLY_CLOSE_PRICE", "BS_CALL_DELTA", "BS_PUT_DELTA",
              "BS_CALL_POP",      "BS_PUT_POP",    "OSS_FRONT_IV",
              "OSS_GEX_REGIME",   "OPT_IV_RANK"]

# No try/except — exception propagates fully
reg.assert_no_missing_indicators(FAKE_TRACE, REQUIRED)
print("SHOULD NOT REACH HERE")
PYEOF
echo "python3 exit code: $?"
cd "$ROOT"

# ══════════════════════════════════════════════════════════════════════════════
sep "ITEM 4 / SECTION 9 — Full uncaught traceback: assert_pattern_scan_complete"
# ══════════════════════════════════════════════════════════════════════════════
echo "python3 (uncaught — no try/except)"
echo "cwd: $PYDIR"
echo ""
cd "$PYDIR"
python3 - <<'PYEOF' 2>&1
import sys
sys.path.insert(0, '.')
import aiem_options_registries as reg

FAKE_TRACE = "VERIFY_FAKE_TRACE_PHASE3_P1_UNCAUGHT_000"

# No try/except
reg.assert_pattern_scan_complete(FAKE_TRACE)
print("SHOULD NOT REACH HERE")
PYEOF
echo "python3 exit code: $?"
cd "$ROOT"

echo ""
echo "--- end of raw evidence ---"
exit 0
