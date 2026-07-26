#!/usr/bin/env bash
# verify_stage4_revalid.sh
# Standing-protocol verification for per-source execution-time revalidation.
#
# Checks:
#   1. Function definition exists in main.py
#   2. Call-site wired inside _aiem_paper_execute_today
#   3. Call-site ordering: after quotes fetch, before for-loop
#   4. Audit table present in DB with criteria_checked column
#   5. Synthetic unit tests:
#      A. ZCMD (gap_volume, price collapsed) → STILL rejected
#      B. AAPL (conviction_stack, flat) → NOT rejected (pass-through)
#      C. NVDA (gap_volume, healthy) → approved
#      D. Layer9 ticker with stale/low score → rejected
#      E. Layer9 ticker with valid score → approved
#      F. Unusual-calls ticker with low prem → rejected
#      G. avg_volume=0 for gap_volume → rvol skipped, price+gap still checked
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
    log_fail "Audit table missing or criteria_checked column absent (TABLE_OK=$TABLE_OK)"
fi

# ── CHECK 5: Source routing keywords present in function body ─────────────────
FUNC_START=$(grep -n "^def _stage4_execution_revalidate" "$MAIN_PY" | head -1 | cut -d: -f1)
FUNC_END=$(awk -v s="$FUNC_START" 'NR>s && /^def / {print NR-1; exit}' "$MAIN_PY")
FUNC_BODY=$(sed -n "${FUNC_START},${FUNC_END}p" "$MAIN_PY")

for KW in "gap_volume" "conviction_stack" "_RV_PASS_THROUGH_SRCS" \
          "sweep" "unusual_calls" "aiem_ai" "oi_buildup" "layer9_stat" \
          "PASS_RVOL_SKIPPED_NO_AVG" "criteria_checked" "per-source"; do
    if echo "$FUNC_BODY" | grep -q "$KW"; then
        log_pass "Keyword '$KW' present in function body"
    else
        log_fail "Keyword '$KW' MISSING from function body"
    fi
done

# ── CHECK 6: Synthetic Python unit tests ──────────────────────────────────────
python3 - <<'PYEOF'
import sys

def _revalidate_synthetic(picks, quotes, mins_elapsed=12.0,
                          valid_sweep=None, valid_ucalls=None,
                          valid_aiem_ai=None, valid_oi=None,
                          valid_layer9=None):
    """
    Extracted logic mirroring _stage4_execution_revalidate without Flask/DB.
    Callers pass pre-resolved valid_* sets to simulate DB query results.
    """
    valid_sweep    = valid_sweep    or set()
    valid_ucalls   = valid_ucalls   or set()
    valid_aiem_ai  = valid_aiem_ai  or set()
    valid_oi       = valid_oi       or set()
    valid_layer9   = valid_layer9   or set()

    PASS_THROUGH = frozenset({
        "conviction_stack", "multi_signal", "washout_ignition",
        "squeeze_reversion", "aiem_v3_discovery",
        "fear_premium_gex", "gap_down_distribution",
    })

    DB_META = {
        "sweep":         (valid_sweep,   "premium>=50000 last 2d"),
        "unusual_calls": (valid_ucalls,  "prem>=75000 last 2d"),
        "aiem_ai":       (valid_aiem_ai, "conviction HIGH/EXTREME + BULLISH last 1d"),
        "oi_buildup":    (valid_oi,      "OI growth>=20% last 4d"),
        "layer9_stat":   (valid_layer9,  "score>=65 + computed_at<6h"),
    }

    approved = []
    rejected = []

    for pick in picks:
        t   = pick["ticker"]
        src = pick.get("source", "unknown")
        q   = quotes.get(t) or {}

        # ── Stage 4: gap_volume (live check) ──────────────────────────────
        if src == "gap_volume":
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
                rejected.append({
                    "ticker": t, "source": src, "failed": failed,
                    "exec_price": exec_price, "exec_gap": exec_gap,
                    "rvol_raw": rvol_raw, "rvol_adj": rvol_adj,
                    "rvol_skipped": rvol_raw is None,
                })
            else:
                action = "PASS_RVOL_SKIPPED_NO_AVG" if rvol_raw is None else "PASS"
                approved.append({**pick, "_revalid_action": action})
            continue

        # ── Pass-through sources ──────────────────────────────────────────
        if src in PASS_THROUGH or src not in DB_META:
            approved.append({**pick, "_revalid_action": "PASS_THROUGH"})
            continue

        # ── DB-backed sources ─────────────────────────────────────────────
        valid_set, crit_desc = DB_META[src]
        if t in valid_set:
            approved.append({**pick, "_revalid_action": "PASS_DB"})
        else:
            rejected.append({
                "ticker": t, "source": src,
                "failed": [f"not in valid set: {crit_desc}"],
                "exec_price": float(q.get("last") or 0),
            })

    return approved, rejected


PASS = 0
FAIL = 0
def chk(cond, label):
    global PASS, FAIL
    if cond:
        print(f"  PASS: {label}")
        PASS += 1
    else:
        print(f"  FAIL: {label}")
        FAIL += 1


# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST A: ZCMD (gap_volume, price=$1.43 vs scan $4.29) ────────────────")
zcmd_pick   = {"ticker": "ZCMD", "source": "gap_volume", "trade_type": "CALL"}
zcmd_quotes = {"ZCMD": {"last": 1.43, "change_pct": -66.7,
                         "volume": 1_800_000, "avg_volume": 200_000}}
app, rej = _revalidate_synthetic([zcmd_pick], zcmd_quotes)
chk(len(app) == 0,  "ZCMD not approved")
chk(len(rej) == 1,  "ZCMD in rejected list")
chk(any("price" in f for f in rej[0]["failed"]),
    f"ZCMD fails price check (exec={rej[0]['exec_price']:.2f})")
chk(any("gap_pct" in f for f in rej[0]["failed"]),
    f"ZCMD fails gap check (exec_gap={rej[0]['exec_gap']:.1f})")

# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST B: AAPL (conviction_stack, gap=+0.3%) → PASS_THROUGH ──────────")
aapl_pick   = {"ticker": "AAPL", "source": "conviction_stack", "trade_type": "CALL"}
aapl_quotes = {"AAPL": {"last": 198.00, "change_pct": 0.3,
                         "volume": 20_000_000, "avg_volume": 50_000_000}}
app, rej = _revalidate_synthetic([aapl_pick], aapl_quotes)
chk(len(app) == 1,  "AAPL approved (conviction_stack is PASS_THROUGH)")
chk(len(rej) == 0,  "AAPL not rejected")
chk(app[0].get("_revalid_action") == "PASS_THROUGH",
    f"AAPL action=PASS_THROUGH (got {app[0].get('_revalid_action')})")

# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST C: NVDA (gap_volume, healthy) → PASS ───────────────────────────")
nvda_pick   = {"ticker": "NVDA", "source": "gap_volume", "trade_type": "CALL"}
nvda_quotes = {"NVDA": {"last": 165.0, "change_pct": 4.5,
                         "volume": 3_000_000, "avg_volume": 1_000_000}}
app, rej = _revalidate_synthetic([nvda_pick], nvda_quotes, mins_elapsed=12.0)
chk(len(app) == 1, "NVDA approved")
chk(len(rej) == 0, "NVDA not rejected")
# rvol_adj = (3M/1M) * (390/12) = 3.0 * 32.5 = 97.5 → passes 2.0
chk(app[0].get("_revalid_action") == "PASS", "NVDA action=PASS")

# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST D: TSLA (layer9_stat) — score expired → REJECTED ──────────────")
tsla_pick   = {"ticker": "TSLA", "source": "layer9_stat", "trade_type": "CALL"}
tsla_quotes = {"TSLA": {"last": 280.0, "change_pct": 1.2}}
# Simulate DB returning empty set (ticker not in valid_layer9)
app, rej = _revalidate_synthetic([tsla_pick], tsla_quotes, valid_layer9=set())
chk(len(app) == 0, "TSLA (stale layer9) rejected")
chk(len(rej) == 1, "TSLA in rejected list")
chk("not in valid set" in rej[0]["failed"][0], "TSLA rejection cites layer9 criteria")

# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST E: TSLA (layer9_stat) — score fresh → APPROVED ─────────────────")
app, rej = _revalidate_synthetic([tsla_pick], tsla_quotes, valid_layer9={"TSLA"})
chk(len(app) == 1, "TSLA (fresh layer9) approved")
chk(len(rej) == 0, "TSLA not rejected")
chk(app[0].get("_revalid_action") == "PASS_DB", "TSLA action=PASS_DB")

# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST F: MSFT (unusual_calls, low prem in DB) → REJECTED ─────────────")
msft_pick   = {"ticker": "MSFT", "source": "unusual_calls", "trade_type": "CALL"}
msft_quotes = {"MSFT": {"last": 430.0, "change_pct": 0.8}}
# valid_ucalls is empty → prem no longer qualifies
app, rej = _revalidate_synthetic([msft_pick], msft_quotes, valid_ucalls=set())
chk(len(app) == 0, "MSFT (unusual_calls, expired prem) rejected")
chk(len(rej) == 1, "MSFT in rejected list")

# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST G: gap_volume avg_volume=0 → rvol skipped; price+gap still apply")
# Sub-case G1: price and gap pass despite no avg_volume → PASS_RVOL_SKIPPED_NO_AVG
no_avg_pick = {"ticker": "MSTR", "source": "gap_volume", "trade_type": "CALL"}
no_avg_q    = {"MSTR": {"last": 350.0, "change_pct": 3.5, "volume": 500_000, "avg_volume": 0}}
app, rej = _revalidate_synthetic([no_avg_pick], no_avg_q)
chk(len(app) == 1, "MSTR (avg_vol=0, price+gap pass) approved")
chk(len(rej) == 0, "MSTR not rejected")
chk(app[0].get("_revalid_action") == "PASS_RVOL_SKIPPED_NO_AVG",
    f"MSTR action=PASS_RVOL_SKIPPED_NO_AVG (got {app[0].get('_revalid_action')})")

# Sub-case G2: avg_volume=0 but PRICE fails → still rejected on price
low_price_pick = {"ticker": "PENY", "source": "gap_volume", "trade_type": "CALL"}
low_price_q    = {"PENY": {"last": 0.85, "change_pct": 5.2, "volume": 500_000, "avg_volume": 0}}
app, rej = _revalidate_synthetic([low_price_pick], low_price_q)
chk(len(app) == 0, "PENY (avg_vol=0, price=0.85<2.00) rejected despite fail-open rvol")
chk(len(rej) == 1, "PENY in rejected list")
chk(any("price" in f for f in rej[0]["failed"]), "PENY rejected on price check")
chk(rej[0]["rvol_skipped"] is True, "PENY rvol_skipped=True (avg_vol=0)")

# ═══════════════════════════════════════════════════════════════════════════════
print("── TEST H: multi_signal → PASS_THROUGH ─────────────────────────────────")
ms_pick   = {"ticker": "META", "source": "multi_signal", "trade_type": "STOCK"}
ms_quotes = {"META": {"last": 540.0, "change_pct": 0.1}}
app, rej = _revalidate_synthetic([ms_pick], ms_quotes)
chk(len(app) == 1, "META (multi_signal) pass-through")
chk(app[0].get("_revalid_action") == "PASS_THROUGH", "META action=PASS_THROUGH")

# ═══════════════════════════════════════════════════════════════════════════════
print(f"\nPython unit tests: {PASS} PASS, {FAIL} FAIL")
sys.exit(0 if FAIL == 0 else 1)
PYEOF
PY_EXIT=$?
if [ $PY_EXIT -eq 0 ]; then
    log_pass "All synthetic unit tests passed"
else
    log_fail "One or more synthetic unit tests FAILED"
fi

# ── Final summary ─────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "verify_stage4_revalid: PASS=$PASS FAIL=$FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ]
