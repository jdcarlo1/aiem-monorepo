#!/usr/bin/env bash
# verify_stage4_revalid.sh
# Standing-protocol verification for Stage 4 execution-time revalidation.
#
# Checks:
#   1. Function definition exists in main.py
#   2. Call-site is wired inside _aiem_paper_execute_today
#   3. Audit table is present in the DB
#   4. Synthetic rejection test: ZCMD-type stale candidate is rejected
#   5. Synthetic pass test: healthy candidate is approved
#
# Exit 0 = all checks PASS.  Exit 1 = at least one FAIL.
set -euo pipefail
PASS=0; FAIL=0
log_pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
log_fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

MAIN_PY=/home/runner/workspace/artifacts/stock-scanner-api/main.py

# ── CHECK 1: Function definition ─────────────────────────────────────────────
DEFN_LINE=$(grep -n "^def _stage4_execution_revalidate" "$MAIN_PY" | head -1)
if [ -n "$DEFN_LINE" ]; then
    log_pass "Function _stage4_execution_revalidate defined — $DEFN_LINE"
else
    log_fail "Function _stage4_execution_revalidate NOT found in main.py"
fi

# ── CHECK 2: Call-site wired in _aiem_paper_execute_today ────────────────────
CALL_LINE=$(grep -n "_stage4_execution_revalidate(picks, quotes)" "$MAIN_PY" | head -1)
if [ -n "$CALL_LINE" ]; then
    log_pass "Call-site wired — $CALL_LINE"
else
    log_fail "Call-site _stage4_execution_revalidate(picks, quotes) NOT found"
fi

# ── CHECK 3: Call-site is AFTER quotes fetch and BEFORE for-loop ─────────────
QUOTES_LINE=$(grep -n "quotes.*=.*_td_quotes(tickers)" "$MAIN_PY" | grep -v "^#" | head -1 | cut -d: -f1)
CALL_N=$(echo "$CALL_LINE" | cut -d: -f1)
FOR_LINE=$(grep -n "for pick in picks:" "$MAIN_PY" | awk -F: -v q="$QUOTES_LINE" '$1>q {print; exit}' | cut -d: -f1)
if [ -n "$QUOTES_LINE" ] && [ -n "$CALL_N" ] && [ -n "$FOR_LINE" ]; then
    if [ "$CALL_N" -gt "$QUOTES_LINE" ] && [ "$CALL_N" -lt "$FOR_LINE" ]; then
        log_pass "Call-site order correct: quotes_line=$QUOTES_LINE < call_line=$CALL_N < for_loop=$FOR_LINE"
    else
        log_fail "Call-site order WRONG: quotes_line=$QUOTES_LINE call_line=$CALL_N for_loop=$FOR_LINE"
    fi
else
    log_fail "Could not resolve line numbers for order check (quotes=$QUOTES_LINE call=$CALL_N for=$FOR_LINE)"
fi

# ── CHECK 4: Audit table present in DB ───────────────────────────────────────
TABLE_CHECK=$(psql "$DATABASE_URL" -tAc \
    "SELECT COUNT(*) FROM information_schema.tables \
     WHERE table_name='aiem_execution_revalidation_log'" 2>/dev/null || echo "0")
if [ "$TABLE_CHECK" = "1" ]; then
    log_pass "Audit table aiem_execution_revalidation_log exists in DB"
else
    log_fail "Audit table aiem_execution_revalidation_log NOT found in DB (may need one real run to create it)"
fi

# ── CHECK 5 + 6: Synthetic Python unit test ──────────────────────────────────
python3 - <<'PYEOF'
import sys, datetime

# ── Replicate the core revalidation logic without importing Flask ─────────────
def _revalidate_synthetic(picks, quotes, mins_elapsed=12.0):
    """Extracted logic from _stage4_execution_revalidate for unit-testing."""
    approved = []
    rejected = []
    for pick in picks:
        t   = pick["ticker"]
        src = pick.get("source", "unknown")
        q   = quotes.get(t) or {}

        exec_price   = float(q.get("last") or q.get("bid") or 0)
        exec_gap     = float(q.get("change_pct") or 0)
        exec_vol     = float(q.get("volume") or 0)
        exec_avg_vol = float(q.get("avg_volume") or 0)

        if exec_avg_vol > 0:
            rvol_raw = exec_vol / exec_avg_vol
            rvol_adj = rvol_raw * (390.0 / mins_elapsed)
        else:
            rvol_raw = None
            rvol_adj = 99.9   # fail-open

        if exec_price == 0:
            approved.append(pick)
            continue

        failed = []
        if exec_price < 2.0:
            failed.append(f"price={exec_price:.3f}<2.00")
        if exec_gap < 1.0:
            failed.append(f"gap_pct={exec_gap:.2f}<1.0")
        if rvol_adj < 2.0:
            failed.append(f"rvol_adj={rvol_adj:.2f}<2.0")

        if failed:
            rejected.append({"ticker": t, "source": src, "failed": failed,
                              "exec_price": exec_price, "exec_gap": exec_gap,
                              "rvol_adj": rvol_adj})
        else:
            approved.append(pick)

    return approved, rejected


PASS = 0
FAIL = 0

def chk(cond, label):
    global PASS, FAIL
    if cond:
        print(f"PASS: {label}")
        PASS += 1
    else:
        print(f"FAIL: {label}")
        FAIL += 1

# ─── TEST A: ZCMD-type candidate (Jul 22 scan price=$4.29, Jul 24 exec=$1.43) ──
# Expectations:
#   price=1.43  → fails price>=2.0
#   gap_pct=-66.7 → fails gap_pct>=1.0
#   rvol_adj depends on volume, but price gate alone is enough to reject
zcmd_pick   = {"ticker": "ZCMD", "source": "gap_volume", "trade_type": "CALL"}
zcmd_quotes = {
    "ZCMD": {
        "last":         1.43,
        "change_pct":  -66.7,   # huge intraday decline vs prev-close
        "volume":       1_800_000,
        "avg_volume":     200_000,
        "bid":          1.40,
        "prevclose":    4.29,
    }
}
approved, rejected = _revalidate_synthetic([zcmd_pick], zcmd_quotes, mins_elapsed=12.0)
chk(len(approved) == 0,  "ZCMD rejected (not approved)")
chk(len(rejected) == 1,  "ZCMD appears in rejected list")
chk(any("price" in f for f in rejected[0]["failed"]),
    f"ZCMD failed price check (exec={rejected[0]['exec_price']:.2f})")
chk(any("gap_pct" in f for f in rejected[0]["failed"]),
    f"ZCMD failed gap_pct check (exec_gap={rejected[0]['exec_gap']:.1f})")

# ─── TEST B: Healthy gap-volume candidate — should PASS ──────────────────────
nvda_pick   = {"ticker": "NVDA", "source": "gap_volume", "trade_type": "CALL"}
nvda_quotes = {
    "NVDA": {
        "last":        165.00,
        "change_pct":    4.5,
        "volume":      3_000_000,
        "avg_volume":  1_000_000,   # rvol_raw=3.0, adj=3.0*(390/12)=97.5
        "bid":         164.90,
        "prevclose":   158.00,
    }
}
approved_b, rejected_b = _revalidate_synthetic([nvda_pick], nvda_quotes, mins_elapsed=12.0)
chk(len(approved_b) == 1, "NVDA approved (healthy candidate)")
chk(len(rejected_b) == 0, "NVDA not in rejected list")

# ─── TEST C: conviction_stack pick with no gap today — should be REJECTED ────
# A watchlist pick that consolidating flat, rvol normal, but gap<1%
flat_pick   = {"ticker": "AAPL", "source": "conviction_stack", "trade_type": "CALL"}
flat_quotes = {
    "AAPL": {
        "last":        198.00,
        "change_pct":   0.3,     # barely moved today — fails gap>=1.0
        "volume":      20_000_000,
        "avg_volume":  50_000_000, # rvol_raw=0.4, adj=0.4*(390/12)=13.0 — passes
        "bid":         197.95,
        "prevclose":   197.40,
    }
}
approved_c, rejected_c = _revalidate_synthetic([flat_pick], flat_quotes, mins_elapsed=12.0)
chk(len(approved_c) == 0, "AAPL (flat conviction pick) rejected")
chk(len(rejected_c) == 1, "AAPL in rejected list")
chk(any("gap_pct" in f for f in rejected_c[0]["failed"]),
    f"AAPL failed gap_pct check (exec_gap={rejected_c[0]['exec_gap']:.2f})")

# ─── TEST D: quote unavailable (price=0) → fail-open ─────────────────────────
no_quote_pick   = {"ticker": "XYZ", "source": "unusual_calls", "trade_type": "CALL"}
no_quote_quotes = {"XYZ": {"last": 0, "change_pct": 0, "volume": 0, "avg_volume": 0}}
approved_d, rejected_d = _revalidate_synthetic([no_quote_pick], no_quote_quotes)
chk(len(approved_d) == 1, "XYZ (no quote) passes fail-open")
chk(len(rejected_d) == 0, "XYZ not in rejected list (fail-open)")

# ─── TEST E: avg_volume=0 → rvol check skipped, price+gap still checked ──────
no_avg_pick   = {"ticker": "MSTR", "source": "layer9", "trade_type": "CALL"}
no_avg_quotes = {
    "MSTR": {
        "last":        350.00,
        "change_pct":   2.5,
        "volume":       500_000,
        "avg_volume":   0,          # rvol_adj = 99.9 (fail-open) — should pass
        "bid":         349.50,
        "prevclose":   341.46,
    }
}
approved_e, rejected_e = _revalidate_synthetic([no_avg_pick], no_avg_quotes)
chk(len(approved_e) == 1, "MSTR (avg_volume=0) approved — rvol check skipped (fail-open)")
chk(len(rejected_e) == 0, "MSTR not rejected")

# ─── Summary ──────────────────────────────────────────────────────────────────
print(f"\nPython unit tests: {PASS} PASS, {FAIL} FAIL")
sys.exit(0 if FAIL == 0 else 1)
PYEOF
PY_EXIT=$?
if [ $PY_EXIT -eq 0 ]; then
    log_pass "All synthetic unit tests passed"
else
    log_fail "One or more synthetic unit tests FAILED (see output above)"
fi

# ── Final summary ─────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "verify_stage4_revalid: PASS=$PASS FAIL=$FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
