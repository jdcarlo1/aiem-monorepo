#!/usr/bin/env bash
# One-time backfill wrapper — runs at 3:05 AM ET after nightly VM reset
# (stock-api exits at 3:00 AM; aiem-process at 3:02; notifier at 3:04)
# Remove this workflow after backfill + verification are confirmed complete.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LOG="$REPO_ROOT/.local/backfill_3am_$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p "$REPO_ROOT/.local"

log() { echo "[$(date -u +%T.%3NZ)] $*" | tee -a "$LOG"; }

# ── 1. Sleep until 3:05 AM ET ─────────────────────────────────────────────
log "Backfill scheduler started. Computing wait time..."
NOW_ET=$(TZ='America/New_York' date +%s)
TODAY_ET=$(TZ='America/New_York' date +%Y%m%d)
TARGET_ET=$(TZ='America/New_York' date -d "${TODAY_ET} 03:05:00" +%s 2>/dev/null \
            || python3 -c "
import datetime, zoneinfo, time
ET = zoneinfo.ZoneInfo('America/New_York')
now = datetime.datetime.now(ET)
t   = now.replace(hour=3, minute=5, second=0, microsecond=0)
if t <= now: t += datetime.timedelta(days=1)
print(int(t.timestamp()))
")
WAIT=$((TARGET_ET - NOW_ET))
if (( WAIT > 0 )); then
    TARGET_STR=$(TZ='America/New_York' date -d "@$TARGET_ET" 2>/dev/null \
                 || python3 -c "
import datetime, zoneinfo
ET = zoneinfo.ZoneInfo('America/New_York')
print(datetime.datetime.fromtimestamp($TARGET_ET, ET).strftime('%Y-%m-%d %H:%M:%S %Z'))
")
    log "Sleeping ${WAIT}s until ${TARGET_STR}  (${WAIT}s = $((WAIT/3600))h $(( (WAIT%3600)/60 ))m)"
    sleep "$WAIT"
else
    log "Already past 3:05 AM ET — running immediately"
fi

log "=== 3 AM BACKFILL STARTING ==="
log "Log file: $LOG"

# ── 2. Run the backfill ────────────────────────────────────────────────────
log "Running backfill_gap_rvol.py (SHA must be 67ffec58)..."
SHA_BF=$(sha256sum "$SCRIPT_DIR/backfill_gap_rvol.py" | cut -c1-8)
log "backfill_gap_rvol.py SHA prefix: $SHA_BF"
if [[ "$SHA_BF" != "67ffec58" ]]; then
    log "ERROR: SHA mismatch — expected 67ffec58, got $SHA_BF. Aborting."
    exit 1
fi

cd "$REPO_ROOT/artifacts/stock-scanner-api"
python3 tools/backfill_gap_rvol.py 2>&1 | tee -a "$LOG"
BACKFILL_EXIT=${PIPESTATUS[0]}
log "backfill_gap_rvol.py exit code: $BACKFILL_EXIT"

if (( BACKFILL_EXIT != 0 )); then
    log "ERROR: backfill script exited non-zero. See log for details."
    exit "$BACKFILL_EXIT"
fi

# ── 3. Post-backfill verification ─────────────────────────────────────────
log ""
log "=== POST-BACKFILL VERIFICATION ==="

python3 - 2>&1 | tee -a "$LOG" <<'PYEOF'
import os, sys, time, datetime, psycopg2
sys.path.insert(0, "/home/runner/workspace/artifacts/stock-scanner-api")
import aiem_discovery_engine as de

DB_URL = os.environ["DATABASE_URL"]

# ── V1: COALESCE short-circuit proof ──────────────────────────────────────
# Query a sample date that should now have stored gap_pct/rvol values.
# If stored, _load_backtest_universe() will use COALESCE(stored, computed).
# We verify by checking that gap_pct IS NOT NULL for a representative date.
print("\n=== V1: COALESCE short-circuit — stored value coverage ===", flush=True)
conn = psycopg2.connect(DB_URL, connect_timeout=5)
conn.autocommit = True
cur = conn.cursor()
# Pick a date well inside the backfill range that should now be fully populated
cur.execute("""
    SELECT scan_date,
           COUNT(*)                   AS total,
           COUNT(gap_pct)             AS gap_nonnull,
           COUNT(rvol)                AS rvol_nonnull,
           ROUND(COUNT(gap_pct)::numeric / COUNT(*) * 100, 2) AS gap_pct_pct
    FROM polygon_market_daily
    WHERE scan_date BETWEEN '2025-01-02' AND '2025-01-31'
    GROUP BY scan_date
    ORDER BY scan_date
    LIMIT 5;
""")
rows = cur.fetchall()
print(f"{'date':<12} {'total':>8} {'gap_nn':>8} {'rvol_nn':>8} {'gap%':>8}")
for r in rows:
    print(f"{str(r[0]):<12} {r[1]:>8,} {r[2]:>8,} {r[3]:>8,} {float(r[4]):>7.2f}%")

# Full-table after-backfill counts
cur.execute("""
    SELECT
        COUNT(*)                             AS total_rows,
        COUNT(gap_pct)                       AS gap_pct_nonnull,
        COUNT(rvol)                          AS rvol_nonnull,
        COUNT(close_strength)                AS close_str_nonnull,
        COUNT(range_pct)                     AS range_pct_nonnull,
        COUNT(*) FILTER (WHERE close_price > 2.0) AS price_ok
    FROM polygon_market_daily
    WHERE scan_date >= '2024-07-22';
""")
r = cur.fetchone()
total = r[0]
print(f"\n=== AFTER BACKFILL — full table (from 2024-07-22) ===")
print(f"  total_rows:        {total:>12,}")
names = ['gap_pct_nonnull','rvol_nonnull','close_str_nonnull','range_pct_nonnull','price_ok']
for name, val in zip(names, r[1:]):
    pct = val/total*100 if total else 0
    print(f"  {name:<20} {val:>12,}  ({pct:.2f}%)")
cur.close(); conn.close()

# ── V2: Full-window discovery cycle timing ────────────────────────────────
print("\n=== V2: Full-window discovery cycle (2024-07-22→today, with stored values) ===", flush=True)
# Override constants to the approved full window
today_str = datetime.date.today().isoformat()
de._TRAIN_START = "2024-07-22"
de._TRAIN_END   = "2025-06-30"
de._TEST_START  = "2025-07-01"
de._TEST_END    = today_str
print(f"Windows: TRAIN={de._TRAIN_START}→{de._TRAIN_END}  TEST={de._TEST_START}→{de._TEST_END}", flush=True)
t0 = time.time()
result = de.run_cycle(trigger_source="post_backfill_verify")
elapsed = time.time() - t0
print(f"run_cycle() completed in {elapsed:.2f}s", flush=True)
print(f"  total_templates : {result.get('total_templates', 'N/A')}")
print(f"  train_n         : {result.get('train_n', 'N/A')}")
print(f"  test_n          : {result.get('test_n', 'N/A')}")
print(f"  run_status      : {result.get('run_status', 'ok')}")
err = result.get('error')
if err:
    print(f"  error           : {err}")
if elapsed > 30:
    print(f"WARNING: elapsed {elapsed:.1f}s > 30s threshold — OOM risk may remain")
elif elapsed < 10:
    print(f"PASS: Full-window cycle completed in {elapsed:.2f}s — COALESCE short-circuiting stored values")
else:
    print(f"INFO: elapsed {elapsed:.1f}s (acceptable; stored values reduce compute)")
PYEOF

log ""
log "=== BACKFILL + VERIFICATION COMPLETE ==="
log "Log saved to: $LOG"
echo ""
echo "NEXT STEP: copy log contents into docs/verification/discovery-cycle-backfill-FINAL.md §12"
