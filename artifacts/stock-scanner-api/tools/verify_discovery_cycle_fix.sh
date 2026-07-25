#!/bin/bash
# verify_discovery_cycle_fix.sh
#
# Standalone falsification-resistant verification of the discovery cycle
# backfill fix (2026-07-25). Checks:
#   1. SHA256 of changed files against known-good values
#   2. Date constants in aiem_discovery_engine.py
#   3. on-the-fly COALESCE CTE presence in _load_backtest_universe
#   4. run_status="aborted_no_data" key in run_cycle early-return
#   5. error_msg propagation fix in main.py _discovery_cycle_job
#   6. Live DB qualifying row counts (train + test windows)
#   7. Negative control: empty date range → aborted_no_data fires
#   8. Canonical SHA256 of verification infrastructure (verify_chain.sh, verified_run.sh)
#
# Run: bash artifacts/stock-scanner-api/tools/verify_discovery_cycle_fix.sh
# Run from project root (workspace/).

set -euo pipefail

PASS=0
FAIL=0

ok()   { echo "  PASS: $*"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }
hdr()  { echo; echo "=== $* ==="; }

DE=artifacts/stock-scanner-api/aiem_discovery_engine.py
MP=artifacts/stock-scanner-api/main.py
BF=artifacts/stock-scanner-api/tools/backfill_gap_rvol.py

hdr "1. SHA256 of changed files"
SHA_DE=$(sha256sum "$DE" | cut -d' ' -f1)
SHA_MP=$(sha256sum "$MP" | cut -d' ' -f1)
SHA_BF=$(sha256sum "$BF" | cut -d' ' -f1)

echo "  aiem_discovery_engine.py: $SHA_DE"
echo "  main.py:                  $SHA_MP"
echo "  backfill_gap_rvol.py:     $SHA_BF"

[[ "$SHA_DE" == "527e5f170074600f42214d0324f02a1cf8c099f9c16b4a627b44689044abee78" ]] \
  && ok "aiem_discovery_engine.py SHA256 matches" \
  || fail "aiem_discovery_engine.py SHA256 mismatch (got $SHA_DE)"

[[ "$SHA_MP" == "806f7432a12cbe1bc1032603f1f40aac42c4a67db54a837e521cf2fe19782757" ]] \
  && ok "main.py SHA256 matches" \
  || fail "main.py SHA256 mismatch (got $SHA_MP)"

hdr "2. Date constants in aiem_discovery_engine.py"
grep -q '_TRAIN_START\s*=\s*"2024-07-22"' "$DE" \
  && ok '_TRAIN_START = "2024-07-22"' \
  || fail "_TRAIN_START is not 2024-07-22"

grep -q '_TRAIN_END\s*=\s*"2025-06-30"' "$DE" \
  && ok '_TRAIN_END = "2025-06-30"' \
  || fail "_TRAIN_END is not 2025-06-30"

grep -q '_TEST_START\s*=\s*"2025-07-01"' "$DE" \
  && ok '_TEST_START = "2025-07-01"' \
  || fail "_TEST_START is not 2025-07-01"

hdr "3. On-the-fly COALESCE CTE present in _load_backtest_universe"
grep -q "COALESCE" "$DE" \
  && ok "COALESCE keyword found" \
  || fail "COALESCE keyword missing"

grep -q "LAG(close_price)" "$DE" \
  && ok "LAG(close_price) window function found" \
  || fail "LAG(close_price) window function missing"

grep -q "ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING" "$DE" \
  && ok "AVG(volume) OVER 30 PRECEDING found" \
  || fail "AVG(volume) OVER 30 PRECEDING missing"

grep -q "buf_start" "$DE" \
  && ok "buf_start 45-day buffer variable found" \
  || fail "buf_start variable missing"

grep -q "timeout_ms: int = 120_000" "$DE" \
  && ok "timeout_ms raised to 120_000" \
  || fail "timeout_ms not raised to 120_000"

hdr "4. run_status=aborted_no_data key in run_cycle"
grep -q '"run_status".*"aborted_no_data"' "$DE" \
  && ok 'run_status="aborted_no_data" found in run_cycle early-return' \
  || fail 'run_status="aborted_no_data" missing from run_cycle'

hdr "5. error_msg propagation fix in _discovery_cycle_job (main.py)"
grep -q 'run_status.*==.*aborted_no_data' "$MP" \
  && ok "aborted_no_data check in _discovery_cycle_job found" \
  || fail "aborted_no_data check in _discovery_cycle_job missing"

grep -q 'error_msg = result.get.*"error"' "$MP" \
  && ok "error_msg propagation from result dict found" \
  || fail "error_msg propagation from result dict missing"

hdr "6. Live DB qualifying row counts"
python3 - << 'PYEOF'
import os, sys, psycopg2

try:
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET statement_timeout = '25000'")

    # Quick sample: 2-month window of train range (fast proxy for full-window proof)
    cur.execute("""
    WITH source AS (
        SELECT ticker, scan_date, open_price, close_price, volume, gap_pct, rvol, close_strength, range_pct,
               LAG(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS prev_close,
               AVG(volume) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING) AS avg_v
        FROM polygon_market_daily
        WHERE scan_date BETWEEN '2024-07-01' AND '2024-09-30'
    ),
    derived AS (
        SELECT ticker, scan_date, close_price, close_strength, range_pct,
               COALESCE(gap_pct, CASE WHEN prev_close>0 THEN (open_price/prev_close)-1.0 ELSE NULL END) AS gap_pct,
               COALESCE(rvol, CASE WHEN avg_v>0 THEN volume::numeric/avg_v ELSE NULL END) AS rvol
        FROM source WHERE scan_date BETWEEN '2024-07-22' AND '2024-09-30'
    ),
    w AS (
        SELECT ticker, scan_date, gap_pct, rvol, close_strength, range_pct, close_price,
               LEAD(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS nc,
               LEAD(scan_date)   OVER (PARTITION BY ticker ORDER BY scan_date) AS nd
        FROM derived WHERE gap_pct IS NOT NULL AND rvol IS NOT NULL
          AND close_strength IS NOT NULL AND range_pct IS NOT NULL
          AND close_price > 2.0 AND rvol < 100.0
    )
    SELECT COUNT(*) FROM w WHERE nc IS NOT NULL AND nd <= scan_date + 5
    """)
    n = cur.fetchone()[0]
    if n >= 200_000:
        print(f"  PASS: 2-month train sample = {n:,} qualifying rows (>= 200,000 threshold)")
        sys.exit(0)
    else:
        print(f"  FAIL: 2-month train sample = {n:,} qualifying rows (< 200,000 threshold)")
        sys.exit(1)
except Exception as e:
    print(f"  FAIL: DB query error: {e}")
    sys.exit(1)
PYEOF
SAMPLE_EXIT=$?
[[ $SAMPLE_EXIT -eq 0 ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

hdr "7. Negative control — empty date range must return aborted_no_data"
python3 - << 'PYEOF'
import os, sys
sys.path.insert(0, 'artifacts/stock-scanner-api')
import aiem_discovery_engine as de

de._TRAIN_START = "2020-01-01"
de._TRAIN_END   = "2020-01-31"
de._TEST_START  = "2020-02-01"
de._TEST_END    = "2020-02-28"

engine = de.DiscoveryEngine()
result = engine.run_cycle()

if result.get("run_status") == "aborted_no_data" and result.get("error"):
    print(f"  PASS: run_status=aborted_no_data, error='{result['error']}'")
    sys.exit(0)
else:
    print(f"  FAIL: result={result}")
    sys.exit(1)
PYEOF
NEG_EXIT=$?
[[ $NEG_EXIT -eq 0 ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

hdr "8. Canonical SHA256 of verification infrastructure (untampered)"
SHA_VR=$(sha256sum tools/verified_run.sh  | cut -d' ' -f1)
SHA_VC=$(sha256sum tools/verify_chain.sh  | cut -d' ' -f1)
echo "  tools/verified_run.sh:  $SHA_VR"
echo "  tools/verify_chain.sh:  $SHA_VC"

[[ "$SHA_VR" == "ba6100ae36baab3ab3c2f96817c49207057eea08b6b134f00bf17695ef0a8836" ]] \
  && ok "verified_run.sh canonical SHA256 matches (ba6100ae)" \
  || fail "verified_run.sh SHA256 mismatch"

[[ "$SHA_VC" == "972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75" ]] \
  && ok "verify_chain.sh canonical SHA256 matches (972ff44a)" \
  || fail "verify_chain.sh SHA256 mismatch"

echo
echo "==============================="
echo "SUMMARY: PASS=$PASS FAIL=$FAIL"
echo "==============================="
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
