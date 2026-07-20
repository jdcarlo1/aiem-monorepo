#!/usr/bin/env bash
# raw_evidence_gaps_phase3p1.sh — Gap closure for Phase III Phase 1 directive.
# Covers: hardcoded constants, assert_data_freshness traceback, before/after SHA256.
# No PASS/FAIL labels. Raw output only. set +e so failure tests don't abort.
set +e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCHED="$ROOT/artifacts/stock-scanner-api/aiem_options_scheduler.py"
REG="$ROOT/artifacts/stock-scanner-api/aiem_options_registries.py"
PIPE="$ROOT/artifacts/stock-scanner-api/aiem_options_pipeline.py"
PYDIR="$ROOT/artifacts/stock-scanner-api"

# ══════════════════════════════════════════════════════════════════════════════
echo "## ITEM 6 — sha256sum canonical check (required header)"
sha256sum "$ROOT/tools/verified_run.sh"
sha256sum "$ROOT/artifacts/stock-scanner-api/verify_chain.sh"
echo "canonical: verified_run.sh=8146a523cdc7fcecdf26451789f6792db8a7091bb0669f07a9c2caf4670119f4"
echo "canonical: verify_chain.sh=ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f"

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "## ITEM 3 — BEFORE/AFTER SHA256 for the three changed files"
echo ""
echo "--- git log --oneline -5 ---"
git --no-optional-locks -C "$ROOT" log --oneline -5
echo ""
echo "--- git show --stat 287b70e (Phase 1 code commit) ---"
git --no-optional-locks -C "$ROOT" show --stat 287b70e
echo ""
echo "--- SHA256 BEFORE (at 287b70e^ = 4f280e6, pre-Phase-1 parent) ---"
echo -n "aiem_options_scheduler.py  BEFORE: "
git --no-optional-locks -C "$ROOT" show 287b70e^:artifacts/stock-scanner-api/aiem_options_scheduler.py 2>/dev/null | sha256sum | awk '{print $1}'
echo -n "aiem_options_pipeline.py   BEFORE: "
git --no-optional-locks -C "$ROOT" show 287b70e^:artifacts/stock-scanner-api/aiem_options_pipeline.py 2>/dev/null | sha256sum | awk '{print $1}'
echo -n "aiem_options_registries.py BEFORE: "
git --no-optional-locks -C "$ROOT" show 287b70e^:artifacts/stock-scanner-api/aiem_options_registries.py 2>/dev/null | sha256sum | awk '{print $1}'
echo "(empty-file SHA = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 means file did not exist in parent)"
echo ""
echo "--- SHA256 AFTER (HEAD = current) ---"
sha256sum "$SCHED" "$REG" "$PIPE"
echo ""
echo "--- git diff 287b70e^ 287b70e --stat (insertions/deletions for these 3 files) ---"
git --no-optional-locks -C "$ROOT" diff 287b70e^ 287b70e -- \
    artifacts/stock-scanner-api/aiem_options_scheduler.py \
    artifacts/stock-scanner-api/aiem_options_registries.py \
    artifacts/stock-scanner-api/aiem_options_pipeline.py

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "## ITEM 1 — HARDCODED NUMERIC CONSTANTS (Phase 1 new code only)"
echo ""
echo "--- Source: aiem_options_registries.py (entire file is Phase 1) ---"
echo ""

echo "=== connect_timeout=6 (bootstrap_registries only) ==="
grep -n "connect_timeout=6" "$REG"
echo "trace: psycopg2 DDL CREATE TABLE can take slightly longer than transactional queries;"
echo "       6s used only for bootstrap (line 79); all other connections use 4s."

echo ""
echo "=== connect_timeout=4 (all other DB calls in registries) ==="
grep -n "connect_timeout=4" "$REG"
echo "trace: 4s matches existing codebase standard (grep main.py for connect_timeout=4)."

echo ""
echo "=== round(..., 4) in capture_options_metrics / enrich_metrics_oss ==="
grep -n "round(" "$REG"
echo "trace: 4 decimal places = standard financial precision for bid/ask/mid/voi;"
echo "       bid, ask come from _execute_job Stage 4 call_bid/call_ask (Tradier chain data);"
echo "       vol/oi come from Stage 4 chain data; rr = profit_target/premium_at_risk."

echo ""
echo "=== ev formula: (1 - prob) * 1.0 ==="
grep -n "(1 - _f" "$REG"
echo "trace: ev = (prob * expected_return) - ((1 - prob) * 1.0)"
echo "       The 1.0 is the normalised loss-per-unit when option expires worthless"
echo "       (i.e., 100% of premium paid). expected_return and probability_estimate"
echo "       come from Stage 4 enrich_metrics_oss call; these are pipeline-computed,"
echo "       not hardcoded (source: aiem_options_intel.py get_put_call_pop / REQ6)."

echo ""
echo "--- Source: aiem_options_scheduler.py — PHASE 1 CAPTURE BLOCKS ONLY (lines 760-1450) ---"
echo ""

echo "=== 172800 (48h stale threshold, lines 764 + 1436) ==="
grep -n "172800" "$SCHED"
echo "trace: 48*3600 seconds = 48h. Polygon EOD data published by 5PM ET;"
echo "       pipeline runs at 9:45 AM next trading day = ~17h gap normal;"
echo "       >48h = missed at least one full trading day = STALE. Passed"
echo "       directly to assert_data_freshness(max_stale_seconds=172800) at line 1436."

echo ""
echo "=== 500.0 normalization (POLY_CLOSE_PRICE) ==="
grep -n "500\.0\|close_price/5" "$SCHED"
echo "trace: normalised_value = min(1.0, close_price/500.0). Scales equity price"
echo "       range [0..500] to [0..1]; stored in oe_indicator_snapshots.normalized_value"
echo "       for display/comparison only. Does NOT gate pipeline decisions."

echo ""
echo "=== 0.6 / 0.4 (close_strength quality labels) ==="
grep -n "close_str > 0\.6\|close_str < 0\.4" "$SCHED"
echo "trace: close_str = (close-low)/(high-low) from polygon_market_daily."
echo "       >0.6 = closed in upper 40% of range = BULLISH label."
echo "       <0.4 = closed in lower 40% of range = BEARISH label."
echo "       Label stored in signal_direction col of oe_indicator_snapshots. Display only."

echo ""
echo "=== 0.40 / 0.20 (front_iv quality labels) ==="
grep -n "front_iv > 0\.40\|front_iv < 0\.20" "$SCHED"
echo "trace: front_iv = front_iv_pct/100.0 from oss[3] (aiem_options_intel.py"
echo "       get_options_structure_scan, col index 3 = front_iv_pct)."
echo "       >0.40 = 40% IV = HIGH_VOL; <0.20 = 20% IV = LOW_VOL. Display label only."

echo ""
echo "=== /100.0 conversions (pct → decimal) ==="
grep -n "/ 100\.0\|/100\.0" "$SCHED" | grep -v "^[0-9]*:.*#"
echo "trace: front_iv_pct/100.0 (line 754, pre-existing) and oss[9]/100.0 (back_iv,"
echo "       line 792) both convert Tradier/Polygon IV from percentage-points to decimal."
echo "       These values come directly from aiem_options_intel.get_options_structure_scan."

echo ""
echo "=== 30.0 normalization (OSS_PC_SKEW_PP) ==="
grep -n "abs(pc_skew_pp)/30\|30\.0" "$SCHED" | grep -v "^[0-9]*:.*#"
echo "trace: normalised_value = min(1.0, abs(pc_skew_pp)/30.0)."
echo "       pc_skew_pp = put_call_skew in percentage-points from oss[6]"
echo "       (aiem_options_intel.get_options_structure_scan). Typical range ±30pp;"
echo "       >30pp is extreme. Display normalization only."

echo ""
echo "=== 17 hours in _pmd_dt (Stage 1) ==="
grep -n "_pmd_dt\b" "$SCHED" | head -5
echo "trace: datetime(pmd[0].year, pmd[0].month, pmd[0].day, 17, 0) = 5PM on scan_date."
echo "       pmd[0] is scan_date from polygon_market_daily (per polygon-market-daily-eod-date-bug"
echo "       memory: pmd uses MAX(scan_date)/LIMIT 1 DESC). 5PM chosen as typical EOD publish time."

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "## ITEM 2 — assert_data_freshness: UNCAUGHT TRACEBACK"
echo ""
echo "--- Step 1: INSERT test row with freshness_seconds=999999 ---"
echo "SQL:"
cat <<'SQL'
INSERT INTO oe_indicator_snapshots
    (trace_id, ticker, scan_date, canonical_id, freshness_seconds, quality_status)
VALUES
    ('VERIFY_FRESHNESS_TEST_PHASE3P1_001', 'TEST', CURRENT_DATE,
     'POLY_CLOSE_PRICE', 999999, 'STALE')
ON CONFLICT DO NOTHING;
SQL

cd "$PYDIR"
python3 -c "
import psycopg2, os
url = os.environ['DATABASE_URL']
with psycopg2.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
    cur.execute('''
        INSERT INTO oe_indicator_snapshots
            (trace_id, ticker, scan_date, canonical_id, freshness_seconds, quality_status)
        VALUES
            (%s, %s, CURRENT_DATE, %s, %s, %s)
        ON CONFLICT DO NOTHING
    ''', ('VERIFY_FRESHNESS_TEST_PHASE3P1_001','TEST','POLY_CLOSE_PRICE',999999,'STALE'))
    conn.commit()
    print(f'rowcount={cur.rowcount}')
" 2>&1
echo ""

echo "--- Step 2: Confirm row exists (raw SQL result) ---"
python3 -c "
import psycopg2, os
url = os.environ['DATABASE_URL']
with psycopg2.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
    cur.execute('''
        SELECT trace_id, canonical_id, freshness_seconds, quality_status, captured_at
        FROM oe_indicator_snapshots
        WHERE trace_id = %s
    ''', ('VERIFY_FRESHNESS_TEST_PHASE3P1_001',))
    rows = cur.fetchall()
    for r in rows:
        print(r)
    print(f'({len(rows)} rows)')
" 2>&1
echo ""

echo "--- Step 3: assert_data_freshness uncaught traceback ---"
echo "python3 -c (no try/except; max_stale_seconds=3600; row has freshness_seconds=999999)"
echo ""
python3 - <<'PYEOF' 2>&1
import sys
sys.path.insert(0, '.')
import aiem_options_registries as reg

# No try/except — full traceback propagates to top level
reg.assert_data_freshness(
    'VERIFY_FRESHNESS_TEST_PHASE3P1_001',
    ['POLY_CLOSE_PRICE'],
    3600    # max_stale_seconds=1h; row has 999999s (~11.5 days) → must raise
)
print("SHOULD NOT REACH HERE")
PYEOF
echo "python3 exit code: $?"
cd "$ROOT"

echo ""
echo "--- _CRITICAL_FRESHNESS_IDS list (grep -n, scheduler line 1418-1423) ---"
grep -n "_CRITICAL_FRESHNESS_IDS\|POLY_CLOSE_PRICE.*OSS_FRONT_IV\|\"POLY_CLOSE_PRICE\"\|\"OSS_FRONT_IV\"" "$SCHED" | grep -A2 "CRITICAL"
echo ""
echo "--- Full assert_data_freshness call in scheduler (line 1436 context) ---"
sed -n '1413,1445p' "$SCHED"

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo "## ITEM 5 — verify_chain.sh output"
cd "$ROOT/artifacts/stock-scanner-api"
bash verify_chain.sh 2>&1
echo "verify_chain.sh exit code: $?"
cd "$ROOT"

echo ""
echo "--- end of raw evidence gaps ---"
exit 0
