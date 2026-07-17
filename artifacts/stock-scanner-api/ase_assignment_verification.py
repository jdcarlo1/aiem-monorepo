#!/usr/bin/env python3
"""
ase_assignment_verification.py
══════════════════════════════
Section 9 — Assignment & Expiration
17-field evidence report for every test.

Tests (A9_T01 – A9_T14):
  A9_T01  Early assignment — deep ITM short call, DTE=1            → HIGH
  A9_T02  Early assignment — OTM short call, DTE=30                → LOW
  A9_T03  Auto exercise    — long call $0.05 ITM at expiry          → EXERCISE
  A9_T04  Auto exercise    — long call $0.005 ITM at expiry         → AMBIGUOUS
  A9_T05  Dividend assign  — short call extrinsic < quarterly div   → HIGH
  A9_T06  Dividend assign  — no ex-div date supplied                → LOW
  A9_T07  Pin risk         — short strike 0.2% from spot, DTE=1    → HIGH
  A9_T08  Pin risk         — short strike 5% from spot              → LOW
  A9_T09  Partial assign   — iron condor, short put assigned         → NAKED short call remains
  A9_T10  Multi-leg assign — short straddle simultaneous            → MEDIUM+
  A9_T11  Expiration       — OTM short call expires worthless        → LAPSE, premium kept
  A9_T12  Expiration       — ITM short call auto-assigned            → ASSIGNED, stock created
  A9_T13  Exercise sim     — long put exercised (explicit)           → net P&L matches intrinsic
  A9_T14  Exercise sim     — long OTM call lapses (explicit LAPSE)  → P&L = −premium
"""
from __future__ import annotations
import sys, os, hashlib, uuid, json
from datetime import datetime, timezone
from typing import List, Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from aiem_strat_engine.legs import (
    Leg, ASSET_CALL, ASSET_PUT, ASSET_STOCK,
    SIDE_LONG, SIDE_SHORT,
)
from aiem_strat_engine.assignment import (
    early_assignment_risk,
    automatic_exercise_check,
    dividend_assignment_risk,
    pin_risk_analysis,
    partial_assignment_impact,
    multi_leg_assignment_analysis,
    expiration_outcome,
    exercise_simulation,
    RISK_LOW, RISK_MEDIUM, RISK_HIGH,
    DECISION_EXERCISE, DECISION_LAPSE, DECISION_ASSIGNED, DECISION_AMBIGUOUS,
    OCC_AUTO_EXERCISE_THRESHOLD,
)

# ─────────────────────────────────────────────────────────────────────────────
RUN_ID     = f"A9_{uuid.uuid4().hex[:12].upper()}"
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

_ASSIGN_SHA  = _sha(os.path.join(_ENGINE_DIR, "assignment.py"))
_LEGS_SHA    = _sha(os.path.join(_ENGINE_DIR, "legs.py"))
_THIS_SHA    = _sha(__file__)

# ─────────────────────────────────────────────────────────────────────────────
# Leg builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def _call(side, strike, delta=None, dte=30, mid=1.50, iv=0.30,
          gamma=None, symbol=None, expiration="2026-08-21") -> Leg:
    return Leg(
        asset_type=ASSET_CALL, side=side, strike=strike,
        delta=delta, gamma=gamma, dte=dte, mid=mid, iv=iv,
        bid=mid - 0.05, ask=mid + 0.05,
        option_symbol=symbol or f"{side[0]}C{strike}",
        expiration=expiration,
    )

def _put(side, strike, delta=None, dte=30, mid=1.50, iv=0.30,
         gamma=None, symbol=None, expiration="2026-08-21") -> Leg:
    return Leg(
        asset_type=ASSET_PUT, side=side, strike=strike,
        delta=delta, gamma=gamma, dte=dte, mid=mid, iv=iv,
        bid=mid - 0.05, ask=mid + 0.05,
        option_symbol=symbol or f"{side[0]}P{strike}",
        expiration=expiration,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Evidence reporter
# ─────────────────────────────────────────────────────────────────────────────

_T = 0

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
):
    global PASS_COUNT, FAIL_COUNT, _T
    _T += 1
    ts     = datetime.now(timezone.utc).isoformat()
    verdict= "PASS" if passed else "FAIL"
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  *** FAIL: {test_id} — {name}", flush=True)

    block = (
        f"{'═'*72}\n"
        f" 01. Test ID        : {test_id}\n"
        f" 02. Module         : assignment.py\n"
        f" 03. Test Name      : {name}\n"
        f" 04. Command        : {command}\n"
        f" 05. Raw Output     : {actual[:200]}\n"
        f" 06. Inputs         : {inputs}\n"
        f" 07. Expected       : {expected}\n"
        f" 08. Actual Result  : {actual[:200]}\n"
        f" 09. Numeric Diff   : {numeric_diff}\n"
        f" 10. Tolerance      : {tolerance}\n"
        f" 11. PASS/FAIL      : {verdict}\n"
        f" 12. Timestamp      : {ts}\n"
        f" 13. Run ID         : {RUN_ID}\n"
        f" 14. Extra          : {extra}\n"
        f" 15. Inputs Hash    : {hashlib.sha256(inputs.encode()).hexdigest()[:16]}\n"
        f" 16. assignment.py  : {_ASSIGN_SHA[:16]}\n"
        f" 17. legs.py SHA    : {_LEGS_SHA[:16]}\n"
        f"{'─'*72}\n"
    )
    OUT.append(block)
    return passed


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def t01_early_assign_deep_itm():
    """Deep ITM short call DTE=1 → HIGH"""
    spot = 100.0
    legs = [_call(SIDE_SHORT, 80.0, delta=-0.92, dte=1, mid=20.10)]
    r    = early_assignment_risk(legs, spot)
    ok   = r["risk_level"] == RISK_HIGH
    return _report(
        test_id="A9_T01",
        name="Early assignment — deep ITM short call DTE=1",
        command="early_assignment_risk([SC@80 delta=-0.92 DTE=1], spot=100)",
        inputs=f"legs=SHORT CALL@80 delta=-0.92 DTE=1; spot={spot}",
        expected=f"risk_level={RISK_HIGH}",
        actual=f"risk_level={r['risk_level']} legs={len(r['at_risk_legs'])}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed=ok,
        extra=f"at_risk_legs={r['at_risk_legs']}",
    )


def t02_early_assign_otm():
    """OTM short call DTE=30 → LOW"""
    spot = 100.0
    legs = [_call(SIDE_SHORT, 115.0, delta=-0.15, dte=30, mid=0.40)]
    r    = early_assignment_risk(legs, spot)
    ok   = r["risk_level"] == RISK_LOW and len(r["at_risk_legs"]) == 0
    return _report(
        test_id="A9_T02",
        name="Early assignment — OTM short call DTE=30",
        command="early_assignment_risk([SC@115 delta=-0.15 DTE=30], spot=100)",
        inputs=f"legs=SHORT CALL@115 delta=-0.15 DTE=30; spot={spot}",
        expected=f"risk_level={RISK_LOW} at_risk_legs=0",
        actual=f"risk_level={r['risk_level']} at_risk_legs={len(r['at_risk_legs'])}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed=ok,
    )


def t03_auto_exercise_itm():
    """Long call $0.05 ITM → EXERCISE"""
    spot = 100.05
    legs = [_call(SIDE_LONG, 100.0, dte=0, mid=0.05)]
    decs = automatic_exercise_check(legs, spot)
    ok   = len(decs) == 1 and decs[0]["decision"] == DECISION_EXERCISE
    intrinsic = decs[0]["intrinsic"]
    return _report(
        test_id="A9_T03",
        name="Auto exercise — long call $0.05 ITM at expiry",
        command="automatic_exercise_check([LC@100], spot=100.05)",
        inputs=f"long call K=100 spot={spot}",
        expected=f"decision={DECISION_EXERCISE} intrinsic>=0.01",
        actual=f"decision={decs[0]['decision']} intrinsic={intrinsic:.4f}",
        numeric_diff=f"|intrinsic - 0.05| = {abs(intrinsic - 0.05):.6f}",
        tolerance="0.001",
        passed=ok and abs(intrinsic - 0.05) < 0.001,
    )


def t04_auto_exercise_below_threshold():
    """Long call $0.005 ITM → AMBIGUOUS (below $0.01 OCC threshold)"""
    spot = 100.005
    legs = [_call(SIDE_LONG, 100.0, dte=0, mid=0.005)]
    decs = automatic_exercise_check(legs, spot)
    ok   = len(decs) == 1 and decs[0]["decision"] == DECISION_AMBIGUOUS
    return _report(
        test_id="A9_T04",
        name="Auto exercise — long call $0.005 ITM (below OCC threshold)",
        command="automatic_exercise_check([LC@100], spot=100.005)",
        inputs=f"long call K=100 spot={spot} OCC_threshold={OCC_AUTO_EXERCISE_THRESHOLD}",
        expected=f"decision={DECISION_AMBIGUOUS}",
        actual=f"decision={decs[0]['decision']} intrinsic={decs[0]['intrinsic']:.4f}",
        numeric_diff=f"intrinsic={decs[0]['intrinsic']:.4f} < threshold={OCC_AUTO_EXERCISE_THRESHOLD}",
        tolerance="exact string match",
        passed=ok,
    )


def t05_dividend_assign_risk_high():
    """Short call extrinsic < quarterly div, ex-div in 2d → HIGH"""
    spot = 100.0
    # Short ITM call: mid=21.0, intrinsic=20.0, extrinsic=1.0
    # Annual div=8.0 → quarterly=2.0 → extrinsic(1.0) < div(2.0) → HIGH
    legs = [_call(SIDE_SHORT, 80.0, delta=-0.90, dte=5, mid=21.0)]
    r    = dividend_assignment_risk(
        legs, spot,
        ex_div_date="2026-07-19",
        annual_dividend=8.0,
        days_to_ex_div=2,
    )
    ok = r["risk_level"] == RISK_HIGH and len(r["at_risk_legs"]) == 1
    at = r["at_risk_legs"][0] if r["at_risk_legs"] else {}
    return _report(
        test_id="A9_T05",
        name="Dividend assignment — extrinsic < quarterly dividend",
        command="dividend_assignment_risk([SC@80 mid=21], spot=100, annual_div=8, days=2)",
        inputs=f"SHORT CALL@80 mid=21 spot={spot} annual_div=8 days_to_ex_div=2",
        expected=f"risk_level={RISK_HIGH} at_risk_legs=1",
        actual=f"risk_level={r['risk_level']} at_risk_legs={len(r['at_risk_legs'])}",
        numeric_diff=f"extrinsic={at.get('extrinsic','?')} quarterly_div={at.get('quarterly_dividend','?')}",
        tolerance="exact string match",
        passed=ok,
        extra=f"shortfall={at.get('shortfall','?')}",
    )


def t06_dividend_assign_no_div():
    """No ex-div date → LOW"""
    spot = 100.0
    legs = [_call(SIDE_SHORT, 90.0, delta=-0.75, dte=5, mid=11.0)]
    r    = dividend_assignment_risk(legs, spot, annual_dividend=0.0, days_to_ex_div=None)
    ok   = r["risk_level"] == RISK_LOW
    return _report(
        test_id="A9_T06",
        name="Dividend assignment — no ex-div date supplied",
        command="dividend_assignment_risk([SC@90], spot=100, annual_div=0)",
        inputs=f"SHORT CALL@90 spot={spot} annual_div=0",
        expected=f"risk_level={RISK_LOW}",
        actual=f"risk_level={r['risk_level']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed=ok,
    )


def t07_pin_risk_high():
    """Short strike 0.2% from spot, DTE=1 → HIGH"""
    spot = 100.0
    legs = [_call(SIDE_SHORT, 100.20, delta=-0.50, dte=1, gamma=0.08)]
    r    = pin_risk_analysis(legs, spot)
    ok   = r["risk_level"] == RISK_HIGH
    pe   = r["pin_events"][0] if r["pin_events"] else {}
    return _report(
        test_id="A9_T07",
        name="Pin risk — short strike 0.2% from spot DTE=1",
        command="pin_risk_analysis([SC@100.20 DTE=1 gamma=0.08], spot=100)",
        inputs=f"SHORT CALL@100.20 DTE=1 gamma=0.08 spot={spot}",
        expected=f"risk_level={RISK_HIGH}",
        actual=f"risk_level={r['risk_level']} pct_from_spot={pe.get('pct_from_spot','?')}%",
        numeric_diff=f"pct_from_spot={pe.get('pct_from_spot','?')} < 0.5%",
        tolerance="exact string match",
        passed=ok,
    )


def t08_pin_risk_low():
    """Short strike 5% from spot → LOW"""
    spot = 100.0
    legs = [_call(SIDE_SHORT, 105.0, delta=-0.30, dte=10, gamma=0.03)]
    r    = pin_risk_analysis(legs, spot)
    ok   = r["risk_level"] == RISK_LOW
    return _report(
        test_id="A9_T08",
        name="Pin risk — short strike 5% from spot",
        command="pin_risk_analysis([SC@105 DTE=10], spot=100)",
        inputs=f"SHORT CALL@105 DTE=10 spot={spot}",
        expected=f"risk_level={RISK_LOW}",
        actual=f"risk_level={r['risk_level']}",
        numeric_diff="N/A",
        tolerance="exact string match",
        passed=ok,
    )


def t09_partial_assign_iron_condor():
    """
    Iron condor: SC@110, SP@90, LC@115, LP@85.
    Assign the short put (index 1) → residual must have uncovered short call.
    """
    spot = 100.0
    legs = [
        _call(SIDE_SHORT, 110.0, delta=-0.25, dte=25, mid=1.20),  # 0
        _put(SIDE_SHORT,  90.0,  delta= 0.25, dte=25, mid=1.20),  # 1 ← assigned
        _call(SIDE_LONG,  115.0, delta= 0.15, dte=25, mid=0.60),  # 2
        _put(SIDE_LONG,   85.0,  delta=-0.15, dte=25, mid=0.60),  # 3
    ]
    r  = partial_assignment_impact(legs, spot, assigned_leg_index=1, assigned_quantity=1)
    ok = (
        r["residual_legs_count"] == 3
        and r["residual_has_naked_short"] is False  # SC@110 still covered by LC@115
    )
    return _report(
        test_id="A9_T09",
        name="Partial assignment — iron condor, short put assigned",
        command="partial_assignment_impact(iron_condor, index=1 [SP@90])",
        inputs="iron condor SC@110 SP@90 LC@115 LP@85; assign SP@90",
        expected="residual_legs=3 residual_has_naked_short=False (SC@110 covered by LC@115)",
        actual=(
            f"residual_legs={r['residual_legs_count']} "
            f"has_naked={r['residual_has_naked_short']} "
            f"risk={r['residual_risk']}"
        ),
        numeric_diff="N/A",
        tolerance="exact bool match",
        passed=ok,
        extra=f"pnl_on_assigned={r['assigned_leg']['pnl_on_assigned_leg']}",
    )


def t10_multi_leg_assign_straddle():
    """Short straddle: both legs should show assignment likelihood."""
    spot = 100.0
    legs = [
        _call(SIDE_SHORT, 100.0, delta=-0.52, dte=2, mid=3.00),
        _put(SIDE_SHORT,  100.0, delta= 0.48, dte=2, mid=2.80),
    ]
    r   = multi_leg_assignment_analysis(legs, spot)
    ok  = (
        r["total_short_legs"] == 2
        and r["overall_risk"] in (RISK_MEDIUM, RISK_HIGH)
    )
    likelihoods = [l["assignment_likelihood"] for l in r["legs_at_risk"]]
    return _report(
        test_id="A9_T10",
        name="Multi-leg assignment — short straddle",
        command="multi_leg_assignment_analysis([SC@100 SP@100 DTE=2], spot=100)",
        inputs="SHORT CALL@100 + SHORT PUT@100 delta≈0.50 DTE=2",
        expected="total_short_legs=2 overall_risk=MEDIUM or HIGH",
        actual=f"total_short_legs={r['total_short_legs']} overall_risk={r['overall_risk']} likelihoods={likelihoods}",
        numeric_diff=f"worst_case_pnl={r['worst_case_simultaneous_pnl']}",
        tolerance="risk in [MEDIUM, HIGH]",
        passed=ok,
    )


def t11_expiry_otm_short_call_lapses():
    """OTM short call at expiry: spot=95, K=100 → LAPSE, premium kept."""
    spot = 95.0
    legs = [_call(SIDE_SHORT, 100.0, dte=0, mid=1.50)]
    r    = expiration_outcome(legs, spot)
    lo   = r["leg_outcomes"][0]
    ok   = lo["decision"] == DECISION_LAPSE and abs(r["net_pnl"] - 150.0) < 0.01
    return _report(
        test_id="A9_T11",
        name="Expiration — OTM short call lapses worthless",
        command="expiration_outcome([SC@100 mid=1.50], spot=95)",
        inputs="SHORT CALL@100 mid=1.50 spot=95 (OTM)",
        expected=f"decision={DECISION_LAPSE} net_pnl=+150.00",
        actual=f"decision={lo['decision']} net_pnl={r['net_pnl']} stock_pos={r['net_stock_position']}",
        numeric_diff=f"|net_pnl - 150| = {abs(r['net_pnl'] - 150.0):.4f}",
        tolerance="0.01",
        passed=ok,
    )


def t12_expiry_itm_short_call_assigned():
    """ITM short call at expiry: spot=105, K=100 → ASSIGNED, stock created."""
    spot = 105.0
    legs = [_call(SIDE_SHORT, 100.0, dte=0, mid=1.50)]
    r    = expiration_outcome(legs, spot)
    lo   = r["leg_outcomes"][0]
    # Pnl = (premium - intrinsic) × 100 = (1.50 - 5.0) × 100 = -350
    ok   = (
        lo["decision"] == DECISION_ASSIGNED
        and r["has_stock_residual"] is True
        and r["net_stock_position"] == -100
        and abs(r["net_pnl"] - (-350.0)) < 0.01
    )
    return _report(
        test_id="A9_T12",
        name="Expiration — ITM short call auto-assigned",
        command="expiration_outcome([SC@100 mid=1.50], spot=105)",
        inputs="SHORT CALL@100 mid=1.50 spot=105 (ITM by 5.0)",
        expected=f"decision={DECISION_ASSIGNED} net_pnl=-350.00 stock_position=-100",
        actual=f"decision={lo['decision']} net_pnl={r['net_pnl']} stock_pos={r['net_stock_position']}",
        numeric_diff=f"|net_pnl - (-350)| = {abs(r['net_pnl'] - (-350.0)):.4f}",
        tolerance="0.01",
        passed=ok,
    )


def t13_exercise_sim_long_put_explicit():
    """Long put explicitly exercised: K=100, spot=90 → P&L = (10 - 2.00) × 100 = 800."""
    spot = 90.0
    legs = [_put(SIDE_LONG, 100.0, dte=0, mid=2.00)]
    r    = exercise_simulation(legs, spot, exercise_decisions={0: DECISION_EXERCISE})
    lr   = r["leg_results"][0]
    expected_pnl = (100.0 - 90.0 - 2.00) * 100   # 800.0
    ok   = lr["action"] == DECISION_EXERCISE and abs(r["total_pnl"] - expected_pnl) < 0.01
    return _report(
        test_id="A9_T13",
        name="Exercise simulation — long put explicitly exercised",
        command="exercise_simulation([LP@100 mid=2.00], spot=90, {0: EXERCISE})",
        inputs="LONG PUT@100 mid=2.00 spot=90; decision={0: EXERCISE}",
        expected=f"action=EXERCISE total_pnl={expected_pnl:.2f}",
        actual=f"action={lr['action']} total_pnl={r['total_pnl']} stock_delta={r['net_stock_delta']}",
        numeric_diff=f"|total_pnl - {expected_pnl}| = {abs(r['total_pnl'] - expected_pnl):.4f}",
        tolerance="0.01",
        passed=ok,
    )


def t14_exercise_sim_otm_call_lapse():
    """Long OTM call explicitly lapsed: K=110, spot=105 → P&L = −premium × 100 = −150."""
    spot = 105.0
    legs = [_call(SIDE_LONG, 110.0, dte=0, mid=1.50)]
    r    = exercise_simulation(legs, spot, exercise_decisions={0: DECISION_LAPSE})
    lr   = r["leg_results"][0]
    expected_pnl = -1.50 * 100  # -150
    ok   = lr["action"] == DECISION_LAPSE and abs(r["total_pnl"] - expected_pnl) < 0.01
    return _report(
        test_id="A9_T14",
        name="Exercise simulation — long OTM call explicitly lapsed",
        command="exercise_simulation([LC@110 mid=1.50], spot=105, {0: LAPSE})",
        inputs="LONG CALL@110 mid=1.50 spot=105 OTM; decision={0: LAPSE}",
        expected=f"action={DECISION_LAPSE} total_pnl={expected_pnl:.2f}",
        actual=f"action={lr['action']} total_pnl={r['total_pnl']}",
        numeric_diff=f"|total_pnl - ({expected_pnl})| = {abs(r['total_pnl'] - expected_pnl):.4f}",
        tolerance="0.01",
        passed=ok,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    t01_early_assign_deep_itm,
    t02_early_assign_otm,
    t03_auto_exercise_itm,
    t04_auto_exercise_below_threshold,
    t05_dividend_assign_risk_high,
    t06_dividend_assign_no_div,
    t07_pin_risk_high,
    t08_pin_risk_low,
    t09_partial_assign_iron_condor,
    t10_multi_leg_assign_straddle,
    t11_expiry_otm_short_call_lapses,
    t12_expiry_itm_short_call_assigned,
    t13_exercise_sim_long_put_explicit,
    t14_exercise_sim_otm_call_lapse,
]


def main():
    print(f"\n{'═'*72}", flush=True)
    print(f" ASE Section 9 — Assignment & Expiration Verification", flush=True)
    print(f" Run ID : {RUN_ID}", flush=True)
    print(f" Time   : {SESSION_TS.isoformat()}", flush=True)
    print(f" SHAs   : assignment={_ASSIGN_SHA[:16]}  legs={_LEGS_SHA[:16]}", flush=True)
    print(f"{'─'*72}", flush=True)

    for fn in TESTS:
        fn()

    summary = (
        f"\n{'═'*72}\n"
        f" SECTION 9 SUMMARY\n"
        f" Run ID      : {RUN_ID}\n"
        f" Total tests : {PASS_COUNT + FAIL_COUNT}\n"
        f" PASS        : {PASS_COUNT}\n"
        f" FAIL        : {FAIL_COUNT}\n"
        f" assignment.py SHA-256 : {_ASSIGN_SHA}\n"
        f" legs.py SHA-256       : {_LEGS_SHA}\n"
        f" this file SHA-256     : {_THIS_SHA}\n"
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
