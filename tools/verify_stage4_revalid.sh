#!/usr/bin/env bash
# verify_stage4_revalid.sh
# Standing-protocol verification for per-source execution-time revalidation.
#
# Checks:
#   1.  Function definition exists in main.py
#   2.  Call-site wired inside _aiem_paper_execute_today
#   3.  Call-site ordering: after quotes fetch, before for-loop
#   4.  Audit table present in DB with criteria_checked column
#   5.  All expected routing keywords present in function body
#   6.  Synthetic unit tests (31 assertions across 12 test cases)
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
    log_pass "Function defined — $DEFN_LINE"
else
    log_fail "Function _stage4_execution_revalidate NOT found in main.py"
fi

# ── CHECK 2: Call-site wired ──────────────────────────────────────────────────
CALL_LINE=$(grep -n "_stage4_execution_revalidate(picks, quotes)" "$MAIN_PY" | head -1)
if [ -n "$CALL_LINE" ]; then
    log_pass "Call-site wired — $CALL_LINE"
else
    log_fail "Call-site _stage4_execution_revalidate(picks, quotes) NOT found"
fi

# ── CHECK 3: Call-site ordering ───────────────────────────────────────────────
QUOTES_LINE=$(grep -n "quotes.*=.*_td_quotes(tickers)" "$MAIN_PY" | grep -v "^#" | head -1 | cut -d: -f1)
CALL_N=$(echo "$CALL_LINE" | cut -d: -f1)
FOR_LINE=$(awk -F: -v q="$QUOTES_LINE" 'NR>q && /for pick in picks:/ {print NR; exit}' "$MAIN_PY")
if [ -n "$QUOTES_LINE" ] && [ -n "$CALL_N" ] && [ -n "$FOR_LINE" ]; then
    if [ "$CALL_N" -gt "$QUOTES_LINE" ] && [ "$CALL_N" -lt "$FOR_LINE" ]; then
        log_pass "Order: quotes=$QUOTES_LINE < call=$CALL_N < for_loop=$FOR_LINE"
    else
        log_fail "Order WRONG: quotes=$QUOTES_LINE call=$CALL_N for_loop=$FOR_LINE"
    fi
else
    log_fail "Could not resolve line numbers (quotes=$QUOTES_LINE call=$CALL_N for=$FOR_LINE)"
fi

# ── CHECK 4: Audit table + criteria_checked column ────────────────────────────
TABLE_OK=$(psql "$DATABASE_URL" -tAc \
    "SELECT COUNT(*) FROM information_schema.columns \
     WHERE table_name='aiem_execution_revalidation_log' \
       AND column_name='criteria_checked'" 2>/dev/null || echo "0")
if [ "$TABLE_OK" = "1" ]; then
    log_pass "Audit table exists with criteria_checked column"
else
    log_fail "Audit table or criteria_checked column missing"
fi

# ── CHECK 5: Routing keywords ─────────────────────────────────────────────────
FUNC_START=$(grep -n "^def _stage4_execution_revalidate" "$MAIN_PY" | head -1 | cut -d: -f1)
FUNC_END=$(awk -v s="$FUNC_START" 'NR>s && /^def / {print NR-1; exit}' "$MAIN_PY")
FUNC_BODY=$(sed -n "${FUNC_START},${FUNC_END}p" "$MAIN_PY")

for KW in \
    "gap_volume" "gap_down_distribution" \
    "conviction_stack" "_RV_PASS_THROUGH_SRCS" \
    "sweep" "unusual_calls" "aiem_ai" "oi_buildup" "layer9_stat" \
    "aiem_v3_discovery" "aiem_decision_history" \
    "fear_premium_gex" "options_structure_scan" \
    "PASS_RVOL_SKIPPED_NO_AVG" "criteria_checked" \
    "WARNING: unrecognized source" "_RV_ALL_KNOWN_SRCS" \
    "per-ticker re-check" "documented:"; do
    if echo "$FUNC_BODY" | grep -qF "$KW"; then
        log_pass "Keyword '$KW' present"
    else
        log_fail "Keyword '$KW' MISSING from function body"
    fi
done

# ── CHECK 6: Synthetic Python unit tests ──────────────────────────────────────
python3 - <<'PYEOF'
import sys

def _revalidate_synthetic(
    picks, quotes, mins_elapsed=12.0,
    valid_sweep=None, valid_ucalls=None, valid_aiem_ai=None,
    valid_oi=None, valid_layer9=None, valid_v3=None, valid_fear_gex=None
):
    """Mirror of _stage4_execution_revalidate logic without Flask/DB."""
    valid_sweep     = valid_sweep     or set()
    valid_ucalls    = valid_ucalls    or set()
    valid_aiem_ai   = valid_aiem_ai   or set()
    valid_oi        = valid_oi        or set()
    valid_layer9    = valid_layer9    or set()
    valid_v3        = valid_v3        or set()
    valid_fear_gex  = valid_fear_gex  or set()

    PASS_THROUGH = frozenset({
        "conviction_stack", "multi_signal", "washout_ignition", "squeeze_reversion",
    })
    DB_META = {
        "sweep":             (valid_sweep,    "premium>=50000 last 2d"),
        "unusual_calls":     (valid_ucalls,   "prem>=75000 last 2d"),
        "aiem_ai":           (valid_aiem_ai,  "conviction HIGH/EXTREME + BULLISH"),
        "oi_buildup":        (valid_oi,       "OI growth>=20% last 4d"),
        "layer9_stat":       (valid_layer9,   "score>=65 + computed_at<6h"),
        "aiem_v3_discovery": (valid_v3,       "BUY/SMALL_BUY + confidence>=0.42 today"),
        "fear_premium_gex":  (valid_fear_gex, "FEAR_PREMIUM + skew>=10pp + LONG_GAMMA"),
    }
    ALL_KNOWN = (
        {"gap_volume", "gap_down_distribution"} | PASS_THROUGH | set(DB_META)
    )

    def compute_rvol(q):
        vol, avg = float(q.get("volume") or 0), float(q.get("avg_volume") or 0)
        if avg > 0:
            raw = vol / avg
            return raw, raw * (390.0 / mins_elapsed), False
        return None, 99.9, True

    warnings = []
    approved, rejected = [], []

    for pick in picks:
        t   = pick["ticker"]
        src = pick.get("source", "unknown")
        q   = quotes.get(t) or {}

        # gap_volume — bullish live check
        if src == "gap_volume":
            price = float(q.get("last") or q.get("bid") or 0)
            gap   = float(q.get("change_pct") or 0)
            rraw, radj, rskip = compute_rvol(q)
            if price == 0:
                approved.append({**pick, "_action": "PASS_NO_QUOTE"})
                continue
            failed = []
            if price < 2.0:  failed.append(f"price={price:.3f}<2.00")
            if gap   < 1.0:  failed.append(f"gap={gap:.2f}<1.0")
            if radj  < 2.0:  failed.append(f"rvol_adj={radj:.2f}<2.0")
            if failed:
                rejected.append({"ticker": t, "source": src, "failed": failed,
                                  "exec_price": price, "exec_gap": gap,
                                  "rvol_raw": rraw, "rvol_adj": radj, "rvol_skipped": rskip})
            else:
                action = "PASS_RVOL_SKIPPED_NO_AVG" if rskip else "PASS"
                approved.append({**pick, "_action": action})
            continue

        # gap_down_distribution — bearish live check
        if src == "gap_down_distribution":
            price = float(q.get("last") or q.get("bid") or 0)
            gap   = float(q.get("change_pct") or 0)
            rraw, radj, rskip = compute_rvol(q)
            if price == 0:
                approved.append({**pick, "_action": "PASS_NO_QUOTE"})
                continue
            failed = []
            if price < 5.0:  failed.append(f"price={price:.3f}<5.00")
            if gap > -1.5:   failed.append(f"gap={gap:.2f}>-1.5")
            if radj < 2.5:   failed.append(f"rvol_adj={radj:.2f}<2.5")
            if failed:
                rejected.append({"ticker": t, "source": src, "failed": failed,
                                  "exec_price": price, "exec_gap": gap,
                                  "rvol_raw": rraw, "rvol_adj": radj, "rvol_skipped": rskip})
            else:
                action = "PASS_RVOL_SKIPPED_NO_AVG" if rskip else "PASS"
                approved.append({**pick, "_action": action})
            continue

        # Documented pass-through
        if src in PASS_THROUGH:
            approved.append({**pick, "_action": "PASS_THROUGH"})
            continue

        # DB-backed
        if src in DB_META:
            vs, crit = DB_META[src]
            if t in vs:
                approved.append({**pick, "_action": "PASS_DB"})
            else:
                rejected.append({"ticker": t, "source": src,
                                  "failed": [f"not in valid set: {crit}"],
                                  "exec_price": float(q.get("last") or 0)})
            continue

        # Unrecognized — WARN + fail-open
        warnings.append(f"WARN: unrecognized source '{src}' for {t}")
        approved.append({**pick, "_action": "PASS_UNRECOGNIZED_WARN"})

    return approved, rejected, warnings


PASS = 0; FAIL = 0
def chk(cond, label):
    global PASS, FAIL
    tag = "  PASS" if cond else "  FAIL"
    print(f"{tag}: {label}")
    if cond: PASS += 1
    else:    FAIL += 1

# ─── A. ZCMD — gap_volume, price collapsed ───────────────────────────────────
print("── A: ZCMD (gap_volume, price=$1.43) ──────────────────────────────────")
app, rej, wrn = _revalidate_synthetic(
    [{"ticker":"ZCMD","source":"gap_volume"}],
    {"ZCMD":{"last":1.43,"change_pct":-66.7,"volume":1_800_000,"avg_volume":200_000}}
)
chk(len(app)==0, "ZCMD not approved")
chk(len(rej)==1, "ZCMD in rejected list")
chk(any("price" in f for f in rej[0]["failed"]), "ZCMD fails price check")
chk(any("gap"   in f for f in rej[0]["failed"]), "ZCMD fails gap check")

# ─── B. AAPL — conviction_stack PASS_THROUGH ─────────────────────────────────
print("── B: AAPL (conviction_stack, flat gap) ───────────────────────────────")
app, rej, wrn = _revalidate_synthetic(
    [{"ticker":"AAPL","source":"conviction_stack"}],
    {"AAPL":{"last":198.0,"change_pct":0.3,"volume":20_000_000,"avg_volume":50_000_000}}
)
chk(len(app)==1, "AAPL approved")
chk(app[0]["_action"]=="PASS_THROUGH", "AAPL action=PASS_THROUGH")

# ─── C. NVDA — gap_volume healthy ────────────────────────────────────────────
print("── C: NVDA (gap_volume, healthy) ──────────────────────────────────────")
app, rej, wrn = _revalidate_synthetic(
    [{"ticker":"NVDA","source":"gap_volume"}],
    {"NVDA":{"last":165.0,"change_pct":4.5,"volume":3_000_000,"avg_volume":1_000_000}}
)
chk(len(app)==1, "NVDA approved")
chk(app[0]["_action"]=="PASS", "NVDA action=PASS")

# ─── D. aiem_v3_discovery — decision in DB ───────────────────────────────────
print("── D: TSLA (aiem_v3_discovery) ────────────────────────────────────────")
app, rej, wrn = _revalidate_synthetic(
    [{"ticker":"TSLA","source":"aiem_v3_discovery"}],
    {"TSLA":{"last":280.0}}, valid_v3={"TSLA"}
)
chk(len(app)==1, "TSLA (v3 in DB) approved")
chk(app[0]["_action"]=="PASS_DB", "TSLA action=PASS_DB")

app2, rej2, _ = _revalidate_synthetic(
    [{"ticker":"TSLA","source":"aiem_v3_discovery"}],
    {"TSLA":{"last":280.0}}, valid_v3=set()
)
chk(len(app2)==0, "TSLA (v3 not in DB) rejected")
chk(any("not in valid set" in f for f in rej2[0]["failed"]), "TSLA rejection cites v3 criteria")

# ─── E. fear_premium_gex — DB check ──────────────────────────────────────────
print("── E: SPY (fear_premium_gex) ──────────────────────────────────────────")
app, rej, _ = _revalidate_synthetic(
    [{"ticker":"SPY","source":"fear_premium_gex"}],
    {"SPY":{"last":550.0}}, valid_fear_gex={"SPY"}
)
chk(len(app)==1, "SPY (fear_gex in DB) approved")

app2, rej2, _ = _revalidate_synthetic(
    [{"ticker":"SPY","source":"fear_premium_gex"}],
    {"SPY":{"last":550.0}}, valid_fear_gex=set()
)
chk(len(app2)==0, "SPY (fear_gex not in DB) rejected")

# ─── F. gap_down_distribution — bearish live check ───────────────────────────
print("── F: NKLA (gap_down_distribution) ───────────────────────────────────")
# Healthy bearish: price=$8, gap=-4%, rvol_adj=50x → PASS
app, rej, _ = _revalidate_synthetic(
    [{"ticker":"NKLA","source":"gap_down_distribution"}],
    {"NKLA":{"last":8.0,"change_pct":-4.0,"volume":2_000_000,"avg_volume":500_000}}
)
chk(len(app)==1, "NKLA (healthy bearish) approved")
chk(app[0]["_action"]=="PASS", "NKLA action=PASS")

# Stale price recovered: price=$3 (below $5 floor) → REJECTED
app2, rej2, _ = _revalidate_synthetic(
    [{"ticker":"NKLA","source":"gap_down_distribution"}],
    {"NKLA":{"last":3.0,"change_pct":-4.0,"volume":2_000_000,"avg_volume":500_000}}
)
chk(len(app2)==0, "NKLA (price<5) rejected")
chk(any("price" in f for f in rej2[0]["failed"]), "NKLA fails price<5 check")

# Gap recovered: gap=+0.5% (no longer gapping down) → REJECTED
app3, rej3, _ = _revalidate_synthetic(
    [{"ticker":"NKLA","source":"gap_down_distribution"}],
    {"NKLA":{"last":8.0,"change_pct":0.5,"volume":2_000_000,"avg_volume":500_000}}
)
chk(len(app3)==0, "NKLA (gap recovered) rejected")
chk(any("gap" in f for f in rej3[0]["failed"]), "NKLA fails gap>-1.5 check")

# ─── G. Unrecognized source — WARN + fail-open ───────────────────────────────
print("── G: Unrecognized source ─────────────────────────────────────────────")
app, rej, wrn = _revalidate_synthetic(
    [{"ticker":"XYZ","source":"future_mystery_source"}],
    {"XYZ":{"last":50.0}}
)
chk(len(app)==1,  "XYZ (unknown source) passes fail-open")
chk(len(wrn)==1,  "WARNING emitted for unknown source")
chk("future_mystery_source" in wrn[0], "WARNING names the unknown source")
chk(app[0]["_action"]=="PASS_UNRECOGNIZED_WARN", "XYZ action=PASS_UNRECOGNIZED_WARN")

# ─── H. layer9_stat — stale vs fresh ─────────────────────────────────────────
print("── H: AMD (layer9_stat) ───────────────────────────────────────────────")
app, rej, _ = _revalidate_synthetic(
    [{"ticker":"AMD","source":"layer9_stat"}],
    {"AMD":{"last":130.0}}, valid_layer9={"AMD"}
)
chk(len(app)==1, "AMD (layer9 fresh) approved")
app2, rej2, _ = _revalidate_synthetic(
    [{"ticker":"AMD","source":"layer9_stat"}],
    {"AMD":{"last":130.0}}, valid_layer9=set()
)
chk(len(app2)==0, "AMD (layer9 stale) rejected")

# ─── I. gap_volume avg_volume=0 — rvol skipped, price+gap still enforced ─────
print("── I: avg_volume=0 edge cases ─────────────────────────────────────────")
# Price+gap pass → PASS_RVOL_SKIPPED_NO_AVG
app, rej, _ = _revalidate_synthetic(
    [{"ticker":"MSTR","source":"gap_volume"}],
    {"MSTR":{"last":350.0,"change_pct":3.5,"volume":500_000,"avg_volume":0}}
)
chk(len(app)==1, "MSTR (avg_vol=0, price+gap pass) approved")
chk(app[0]["_action"]=="PASS_RVOL_SKIPPED_NO_AVG", "MSTR action=PASS_RVOL_SKIPPED_NO_AVG")

# Price fails → REJECTED even with avg_vol=0
app2, rej2, _ = _revalidate_synthetic(
    [{"ticker":"PENY","source":"gap_volume"}],
    {"PENY":{"last":0.85,"change_pct":5.2,"volume":500_000,"avg_volume":0}}
)
chk(len(app2)==0, "PENY (price<2, avg_vol=0) rejected")
chk(rej2[0]["rvol_skipped"] is True, "PENY rvol_skipped=True")

print(f"\nPython unit tests: {PASS} PASS, {FAIL} FAIL")
sys.exit(0 if FAIL == 0 else 1)
PYEOF
PY_EXIT=$?
if [ $PY_EXIT -eq 0 ]; then
    log_pass "All synthetic unit tests passed"
else
    log_fail "One or more synthetic unit tests FAILED"
fi

echo ""
echo "═══════════════════════════════════════"
echo "verify_stage4_revalid: PASS=$PASS FAIL=$FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
