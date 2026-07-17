#!/usr/bin/env python3
"""
ase_risk_classification_verification.py
════════════════════════════════════════
Section 10 — Risk Classification
17-field evidence report for every test.

Tests (R10_T01 – R10_T10):
  R10_T01  Naked Call                   → UNDEFINED_RISK, REJECTED
  R10_T02  Naked Put                    → UNDEFINED_RISK, REJECTED
  R10_T03  Naked Straddle               → UNDEFINED_RISK, REJECTED
  R10_T04  Naked Strangle               → UNDEFINED_RISK, REJECTED
  R10_T05  Naked Ratio (2x1 call spread)→ UNDEFINED_RISK, REJECTED
  R10_T06  Unlimited-Loss Synthetic     → UNDEFINED_RISK, REJECTED
  R10_T07  Missing Buying Power         → UNDEFINED_RISK, REJECTED
  R10_T08  Unknown Max Loss             → LIMITED_RISK,   ANALYSIS_ONLY
  R10_T09  Excessive Risk               → LIMITED_RISK,   ANALYSIS_ONLY
  R10_T10  Defined-risk bull call spread→ DEFINED_RISK,   AUTONOMOUS   (control)
"""
from __future__ import annotations
import sys, os, hashlib, uuid
from datetime import datetime, timezone
from typing import List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from aiem_strat_engine.legs import (
    Leg, ASSET_CALL, ASSET_PUT, ASSET_STOCK,
    SIDE_LONG, SIDE_SHORT,
    RISK_DEFINED, RISK_LIMITED, RISK_UNDEFINED,
    MODE_AUTONOMOUS, MODE_ANALYSIS_ONLY,
)
from aiem_strat_engine.risk_classifier import (
    classify_strategy_risk,
    is_naked_call, is_naked_put,
    is_naked_straddle, is_naked_strangle,
    is_naked_ratio,
    is_unlimited_loss_synthetic,
    has_missing_buying_power,
    has_unknown_max_loss,
    is_excessive_risk,
    MODE_REJECTED,
    FLAG_NAKED_CALL, FLAG_NAKED_PUT,
    FLAG_NAKED_STRADDLE, FLAG_NAKED_STRANGLE,
    FLAG_NAKED_RATIO, FLAG_UNLIMITED_LOSS_SYNTHETIC,
    FLAG_MISSING_BUYING_POWER, FLAG_UNKNOWN_MAX_LOSS,
    FLAG_EXCESSIVE_RISK,
)
from aiem_strat_engine.config import (
    MAX_CAPITAL_PER_TRADE, PORTFOLIO_CAPITAL, MAX_CAPITAL_AT_RISK_PCT,
)

# ─────────────────────────────────────────────────────────────────────────────
RUN_ID     = f"R10_{uuid.uuid4().hex[:12].upper()}"
SESSION_TS = datetime.now(timezone.utc)
PASS_COUNT = 0
FAIL_COUNT = 0
OUT        = []

_ENGINE_DIR = os.path.join(os.path.dirname(__file__), "aiem_strat_engine")

def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

_RC_SHA   = _sha(os.path.join(_ENGINE_DIR, "risk_classifier.py"))
_LEGS_SHA = _sha(os.path.join(_ENGINE_DIR, "legs.py"))
_THIS_SHA = _sha(__file__)

# ─────────────────────────────────────────────────────────────────────────────
# Leg builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def _call(side, strike, mid=1.50, delta=None, ratio=1,
          expiration="2026-08-21", symbol=None) -> Leg:
    return Leg(
        asset_type=ASSET_CALL, side=side, strike=strike,
        delta=delta, dte=30, mid=mid, iv=0.30,
        bid=mid - 0.05, ask=mid + 0.05,
        ratio=ratio,
        option_symbol=symbol or f"{side[0]}C{strike}",
        expiration=expiration,
    )

def _put(side, strike, mid=1.50, delta=None, ratio=1,
         expiration="2026-08-21", symbol=None) -> Leg:
    return Leg(
        asset_type=ASSET_PUT, side=side, strike=strike,
        delta=delta, dte=30, mid=mid, iv=0.30,
        bid=mid - 0.05, ask=mid + 0.05,
        ratio=ratio,
        option_symbol=symbol or f"{side[0]}P{strike}",
        expiration=expiration,
    )

def _stock(side) -> Leg:
    return Leg(
        asset_type=ASSET_STOCK, side=side,
        strike=100.0, mid=100.0, dte=None,
        bid=99.95, ask=100.05,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Evidence reporter
# ─────────────────────────────────────────────────────────────────────────────

def _report(
    *,
    test_id:    str,
    name:       str,
    command:    str,
    inputs:     str,
    expected:   str,
    actual:     str,
    numeric_diff: str,
    tolerance:  str,
    passed:     bool,
    extra:      str = "",
) -> bool:
    global PASS_COUNT, FAIL_COUNT
    ts      = datetime.now(timezone.utc).isoformat()
    verdict = "PASS" if passed else "FAIL"
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  *** FAIL: {test_id} — {name}", flush=True)

    block = (
        f"{'═'*72}\n"
        f" 01. Test ID            : {test_id}\n"
        f" 02. Module             : risk_classifier.py\n"
        f" 03. Test Name          : {name}\n"
        f" 04. Command            : {command}\n"
        f" 05. Raw Output         : {actual[:200]}\n"
        f" 06. Inputs             : {inputs}\n"
        f" 07. Expected           : {expected}\n"
        f" 08. Actual Result      : {actual[:200]}\n"
        f" 09. Numeric Diff       : {numeric_diff}\n"
        f" 10. Tolerance          : {tolerance}\n"
        f" 11. PASS/FAIL          : {verdict}\n"
        f" 12. Timestamp          : {ts}\n"
        f" 13. Run ID             : {RUN_ID}\n"
        f" 14. Extra              : {extra}\n"
        f" 15. Inputs Hash        : {hashlib.sha256(inputs.encode()).hexdigest()[:16]}\n"
        f" 16. risk_classifier SHA: {_RC_SHA[:16]}\n"
        f" 17. legs.py SHA        : {_LEGS_SHA[:16]}\n"
        f"{'─'*72}\n"
    )
    OUT.append(block)
    return passed


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def t01_naked_call():
    """Single short call with no hedge → REJECTED, NAKED_CALL, UNDEFINED_RISK."""
    legs = [_call(SIDE_SHORT, 105.0, mid=1.20)]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = is_naked_call(legs)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_REJECTED
        and r["risk_class"]     == RISK_UNDEFINED
        and FLAG_NAKED_CALL in r["risk_flags"]
    )
    return _report(
        test_id  ="R10_T01",
        name     ="Naked Call",
        command  ="classify_strategy_risk([SHORT CALL@105], max_loss=None)",
        inputs   ="legs=[SHORT CALL@105 mid=1.20]; max_loss=None",
        expected =f"execution_mode={MODE_REJECTED} risk_class={RISK_UNDEFINED} flag={FLAG_NAKED_CALL}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"is_naked_call={detect} reason={msg}",
    )


def t02_naked_put():
    """Single short put with no hedge → REJECTED, NAKED_PUT, UNDEFINED_RISK."""
    legs = [_put(SIDE_SHORT, 95.0, mid=1.20)]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = is_naked_put(legs)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_REJECTED
        and r["risk_class"]     == RISK_UNDEFINED
        and FLAG_NAKED_PUT in r["risk_flags"]
    )
    return _report(
        test_id  ="R10_T02",
        name     ="Naked Put",
        command  ="classify_strategy_risk([SHORT PUT@95], max_loss=None)",
        inputs   ="legs=[SHORT PUT@95 mid=1.20]; max_loss=None",
        expected =f"execution_mode={MODE_REJECTED} risk_class={RISK_UNDEFINED} flag={FLAG_NAKED_PUT}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"is_naked_put={detect} reason={msg}",
    )


def t03_naked_straddle():
    """Short call + short put same strike, no hedges → REJECTED, NAKED_STRADDLE."""
    legs = [
        _call(SIDE_SHORT, 100.0, mid=2.50),
        _put(SIDE_SHORT,  100.0, mid=2.50),
    ]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = is_naked_straddle(legs)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_REJECTED
        and r["risk_class"]     == RISK_UNDEFINED
        and FLAG_NAKED_STRADDLE in r["risk_flags"]
    )
    return _report(
        test_id  ="R10_T03",
        name     ="Naked Straddle",
        command  ="classify_strategy_risk([SC@100, SP@100], max_loss=None)",
        inputs   ="legs=[SHORT CALL@100, SHORT PUT@100]; same strike, no hedges",
        expected =f"execution_mode={MODE_REJECTED} flag={FLAG_NAKED_STRADDLE}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"is_naked_straddle={detect} reason={msg}",
    )


def t04_naked_strangle():
    """Short call @105 + short put @95, no hedges → REJECTED, NAKED_STRANGLE."""
    legs = [
        _call(SIDE_SHORT, 105.0, mid=1.20),
        _put(SIDE_SHORT,   95.0, mid=1.20),
    ]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = is_naked_strangle(legs)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_REJECTED
        and r["risk_class"]     == RISK_UNDEFINED
        and FLAG_NAKED_STRANGLE in r["risk_flags"]
    )
    return _report(
        test_id  ="R10_T04",
        name     ="Naked Strangle",
        command  ="classify_strategy_risk([SC@105, SP@95], max_loss=None)",
        inputs   ="legs=[SHORT CALL@105, SHORT PUT@95]; different strikes, no hedges",
        expected =f"execution_mode={MODE_REJECTED} flag={FLAG_NAKED_STRANGLE}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"is_naked_strangle={detect} reason={msg}",
    )


def t05_naked_ratio():
    """
    1×2 call spread: buy 1 call@100, sell 2 calls@105.
    Net uncovered = 1 short call → REJECTED, NAKED_RATIO.
    """
    legs = [
        _call(SIDE_LONG,  100.0, mid=3.00, ratio=1),
        _call(SIDE_SHORT, 105.0, mid=1.50, ratio=2),
    ]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = is_naked_ratio(legs)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_REJECTED
        and r["risk_class"]     == RISK_UNDEFINED
        and FLAG_NAKED_RATIO in r["risk_flags"]
    )
    return _report(
        test_id  ="R10_T05",
        name     ="Naked Ratio (1×2 call spread)",
        command  ="classify_strategy_risk([LC@100 ×1, SC@105 ×2], max_loss=None)",
        inputs   ="legs=[LONG CALL@100 ratio=1, SHORT CALL@105 ratio=2]",
        expected =f"execution_mode={MODE_REJECTED} flag={FLAG_NAKED_RATIO}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"is_naked_ratio={detect} reason={msg}",
    )


def t06_unlimited_loss_synthetic():
    """
    Synthetic short: short call@100 + long put@100, same expiry, no stock hedge
    → REJECTED, UNLIMITED_LOSS_SYNTHETIC.
    """
    legs = [
        _call(SIDE_SHORT, 100.0, mid=3.00, expiration="2026-08-21"),
        _put(SIDE_LONG,   100.0, mid=3.00, expiration="2026-08-21"),
    ]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = is_unlimited_loss_synthetic(legs)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_REJECTED
        and r["risk_class"]     == RISK_UNDEFINED
        and FLAG_UNLIMITED_LOSS_SYNTHETIC in r["risk_flags"]
    )
    return _report(
        test_id  ="R10_T06",
        name     ="Unlimited-Loss Synthetic (synthetic short stock)",
        command  ="classify_strategy_risk([SC@100, LP@100 same expiry], max_loss=None)",
        inputs   ="SHORT CALL@100 + LONG PUT@100 same strike & expiry; no long stock",
        expected =f"execution_mode={MODE_REJECTED} flag={FLAG_UNLIMITED_LOSS_SYNTHETIC}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"detected={detect} reason={msg}",
    )


def t07_missing_buying_power():
    """
    Option leg with strike=None → can't compute buying power → REJECTED.
    """
    # Build leg with strike=None directly
    bad_leg = Leg(
        asset_type=ASSET_CALL, side=SIDE_SHORT,
        strike=None,   # ← missing
        mid=None,      # ← also missing
        dte=30, iv=0.30, bid=None, ask=None,
    )
    legs = [bad_leg]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = has_missing_buying_power(legs)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_REJECTED
        and FLAG_MISSING_BUYING_POWER in r["risk_flags"]
    )
    return _report(
        test_id  ="R10_T07",
        name     ="Missing Buying Power (no strike or mid on short option)",
        command  ="classify_strategy_risk([SC strike=None mid=None], max_loss=None)",
        inputs   ="SHORT CALL strike=None mid=None",
        expected =f"execution_mode={MODE_REJECTED} flag={FLAG_MISSING_BUYING_POWER}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"detected={detect} reason={msg}",
    )


def t08_unknown_max_loss():
    """
    Valid legs (defined spread) but max_loss=None from payoff engine
    → ANALYSIS_ONLY (not REJECTED).
    """
    # A bull call spread with valid legs — but payoff engine returned None
    legs = [
        _call(SIDE_LONG,  100.0, mid=3.00),
        _call(SIDE_SHORT, 105.0, mid=1.50),
    ]
    r    = classify_strategy_risk(legs, max_loss=None)

    detect, msg = has_unknown_max_loss(None)
    ok = (
        detect is True
        and r["execution_mode"] == MODE_ANALYSIS_ONLY
        and r["risk_class"]     == RISK_LIMITED
        and FLAG_UNKNOWN_MAX_LOSS in r["risk_flags"]
        and r["execution_mode"] != MODE_REJECTED  # must NOT be rejected
    )
    return _report(
        test_id  ="R10_T08",
        name     ="Unknown Max Loss (payoff engine returned None)",
        command  ="classify_strategy_risk([LC@100, SC@105], max_loss=None)",
        inputs   ="bull call spread LC@100 SC@105 (valid legs); max_loss=None from payoff",
        expected =f"execution_mode={MODE_ANALYSIS_ONLY} risk_class={RISK_LIMITED} flag={FLAG_UNKNOWN_MAX_LOSS}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"detected={detect} can_paper_trade={r['can_paper_trade']}",
    )


def t09_excessive_risk():
    """
    Valid defined-risk spread but max_loss exceeds per-trade cap
    → ANALYSIS_ONLY (not REJECTED).
    """
    # max_loss × 100 must exceed MAX_CAPITAL_PER_TRADE
    excessive_max_loss = MAX_CAPITAL_PER_TRADE / 100 + 1.0   # just over the cap
    legs = [
        _call(SIDE_LONG,  100.0, mid=60.00),
        _call(SIDE_SHORT, 150.0, mid=10.00),
    ]
    r    = classify_strategy_risk(legs, max_loss=excessive_max_loss)

    detect, msg = is_excessive_risk(excessive_max_loss)
    bp   = excessive_max_loss * 100
    ok = (
        detect is True
        and r["execution_mode"] == MODE_ANALYSIS_ONLY
        and r["risk_class"]     == RISK_LIMITED
        and FLAG_EXCESSIVE_RISK in r["risk_flags"]
        and r["execution_mode"] != MODE_REJECTED
    )
    return _report(
        test_id  ="R10_T09",
        name     ="Excessive Risk (buying power exceeds per-trade cap)",
        command  =f"classify_strategy_risk([LC@100, SC@150], max_loss={excessive_max_loss:.2f})",
        inputs   =f"max_loss={excessive_max_loss:.2f} → buying_power=${bp:,.0f} > cap=${MAX_CAPITAL_PER_TRADE:,.0f}",
        expected =f"execution_mode={MODE_ANALYSIS_ONLY} flag={FLAG_EXCESSIVE_RISK}",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']}",
        numeric_diff=f"buying_power={bp:,.0f} cap={MAX_CAPITAL_PER_TRADE:,.0f} excess={bp - MAX_CAPITAL_PER_TRADE:,.0f}",
        tolerance="exact string match",
        passed   =ok,
        extra    =f"detected={detect} reason={msg}",
    )


def t10_defined_risk_control():
    """
    Control: bull call spread with finite max_loss within caps
    → DEFINED_RISK, AUTONOMOUS, no flags.
    """
    legs = [
        _call(SIDE_LONG,  100.0, mid=3.00),
        _call(SIDE_SHORT, 105.0, mid=1.50),
    ]
    max_loss = 1.50   # debit paid = max loss; $150 buying power → well within cap
    r    = classify_strategy_risk(legs, max_loss=max_loss)

    ok = (
        r["execution_mode"] == MODE_AUTONOMOUS
        and r["risk_class"]  == RISK_DEFINED
        and len(r["risk_flags"]) == 0
        and r["can_paper_trade"] is True
    )
    return _report(
        test_id  ="R10_T10",
        name     ="Defined-risk bull call spread (control case — AUTONOMOUS)",
        command  ="classify_strategy_risk([LC@100, SC@105], max_loss=1.50)",
        inputs   ="bull call spread LC@100 SC@105; max_loss=1.50 ($150 BP)",
        expected =f"execution_mode={MODE_AUTONOMOUS} risk_class={RISK_DEFINED} flags=[]",
        actual   =f"mode={r['execution_mode']} class={r['risk_class']} flags={r['risk_flags']} can_paper={r['can_paper_trade']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed   =ok,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    t01_naked_call,
    t02_naked_put,
    t03_naked_straddle,
    t04_naked_strangle,
    t05_naked_ratio,
    t06_unlimited_loss_synthetic,
    t07_missing_buying_power,
    t08_unknown_max_loss,
    t09_excessive_risk,
    t10_defined_risk_control,
]


def main():
    print(f"\n{'═'*72}", flush=True)
    print(f" ASE Section 10 — Risk Classification Verification", flush=True)
    print(f" Run ID : {RUN_ID}", flush=True)
    print(f" Time   : {SESSION_TS.isoformat()}", flush=True)
    print(f" SHAs   : risk_classifier={_RC_SHA[:16]}  legs={_LEGS_SHA[:16]}", flush=True)
    print(f" Config : MAX_CAPITAL_PER_TRADE={MAX_CAPITAL_PER_TRADE} PORTFOLIO={PORTFOLIO_CAPITAL}", flush=True)
    print(f"{'─'*72}", flush=True)

    for fn in TESTS:
        fn()

    summary = (
        f"\n{'═'*72}\n"
        f" SECTION 10 SUMMARY\n"
        f" Run ID        : {RUN_ID}\n"
        f" Total tests   : {PASS_COUNT + FAIL_COUNT}\n"
        f" PASS          : {PASS_COUNT}\n"
        f" FAIL          : {FAIL_COUNT}\n"
        f" risk_classifier.py SHA-256 : {_RC_SHA}\n"
        f" legs.py SHA-256            : {_LEGS_SHA}\n"
        f" this file SHA-256          : {_THIS_SHA}\n"
        f"{'═'*72}\n"
    )
    OUT.append(summary)
    full = "\n".join(OUT)
    print(full, flush=True)

    out_path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "evidence_chain.log")
    try:
        with open(out_path, "a") as fh:
            fh.write(full + "\n")
    except Exception:
        pass

    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
