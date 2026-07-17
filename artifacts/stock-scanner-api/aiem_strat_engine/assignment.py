"""
assignment.py — Assignment, expiration, and exercise simulation.

Models:
  - Early assignment risk (ITM depth, DTE, dividend timing)
  - OCC automatic exercise rules at expiry ($0.01 ITM threshold)
  - Dividend-driven assignment risk (extrinsic < quarterly dividend)
  - Pin risk (short strike near spot, high gamma near expiry)
  - Partial assignment impact (residual position after one leg assigned)
  - Multi-leg assignment analysis (worst-case simultaneous assignment)
  - Full expiration outcome simulation
  - Exercise decision simulation (override OCC auto-exercise)

All functions are pure: no I/O, no DB calls. Operate on List[Leg].
"""
from __future__ import annotations
import math
from typing import List, Optional, Dict, Any

from .legs import Leg, ASSET_CALL, ASSET_PUT, ASSET_STOCK, SIDE_LONG, SIDE_SHORT

# ── OCC automatic exercise threshold ─────────────────────────────────────────
OCC_AUTO_EXERCISE_THRESHOLD = 0.01   # $0.01 ITM → auto-exercised

# ── Risk level constants ──────────────────────────────────────────────────────
RISK_LOW    = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH   = "HIGH"

# ── Exercise decision constants ───────────────────────────────────────────────
DECISION_EXERCISE    = "EXERCISE"
DECISION_LAPSE       = "LAPSE"
DECISION_ASSIGNED    = "ASSIGNED"
DECISION_NOT_ASSIGNED= "NOT_ASSIGNED"
DECISION_AMBIGUOUS   = "AMBIGUOUS"  # exactly at-the-money within float precision


# ── Internal helpers ──────────────────────────────────────────────────────────

def _option_legs(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type in (ASSET_CALL, ASSET_PUT)]


def _intrinsic(leg: Leg, spot: float) -> float:
    """Intrinsic value at spot (long perspective, always >= 0)."""
    k = leg.strike or 0.0
    if leg.asset_type == ASSET_CALL:
        return max(0.0, spot - k)
    if leg.asset_type == ASSET_PUT:
        return max(0.0, k - spot)
    return 0.0


def _extrinsic(leg: Leg, spot: float) -> Optional[float]:
    """Extrinsic (time) value. None if leg has no mid price."""
    if leg.mid is None:
        return None
    return max(0.0, leg.mid - _intrinsic(leg, spot))


# ── Public API ────────────────────────────────────────────────────────────────

def early_assignment_risk(
    legs: List[Leg],
    spot: float,
    days_to_ex_div: Optional[int] = None,
    annual_dividend: float = 0.0,
) -> Dict[str, Any]:
    """
    Assess early assignment risk across all short option legs.

    Risk elevates when:
    1. Short option is deep ITM (|delta| >= 0.65)
    2. DTE is very short (1-3 days)
    3. For short calls: extrinsic < quarterly dividend and ex-div is imminent

    Returns:
        risk_level      : LOW / MEDIUM / HIGH
        at_risk_legs    : per-leg detail for legs with detected risk
        dividend_driven : True if ex-div timing is the primary driver
        recommendation  : human-readable guidance string
    """
    at_risk = []
    dividend_driven = False

    for lg in _option_legs(legs):
        if lg.side != SIDE_SHORT:
            continue
        abs_delta = abs(lg.delta or 0.0)
        dte       = lg.dte if lg.dte is not None else 999
        intrinsic = _intrinsic(lg, spot)
        extrinsic = _extrinsic(lg, spot)
        strike    = lg.strike or 0.0
        factors   = []
        level     = RISK_LOW

        # Deep ITM
        if abs_delta >= 0.80:
            factors.append(f"deep ITM delta={abs_delta:.2f}")
            level = RISK_HIGH
        elif abs_delta >= 0.65:
            factors.append(f"ITM delta={abs_delta:.2f}")
            level = RISK_MEDIUM

        # Very short DTE with ITM
        if dte <= 1 and intrinsic > 0:
            factors.append(f"DTE={dte} expiry imminent")
            level = RISK_HIGH
        elif dte <= 3 and abs_delta >= 0.65:
            factors.append(f"DTE={dte} short-dated")
            if level == RISK_LOW:
                level = RISK_MEDIUM

        # Dividend-driven (short calls only)
        if (lg.asset_type == ASSET_CALL
                and days_to_ex_div is not None
                and annual_dividend > 0
                and days_to_ex_div <= 3
                and intrinsic > 0):
            q_div = annual_dividend / 4.0
            if extrinsic is not None and extrinsic < q_div:
                factors.append(
                    f"ex-div in {days_to_ex_div}d extrinsic={extrinsic:.4f} < div={q_div:.4f}"
                )
                level = RISK_HIGH
                dividend_driven = True

        if factors:
            at_risk.append({
                "leg":        lg.option_symbol or f"{lg.side} {lg.asset_type}@{strike}",
                "factors":    factors,
                "risk_level": level,
                "intrinsic":  round(intrinsic, 4),
                "extrinsic":  round(extrinsic, 4) if extrinsic is not None else None,
                "dte":        dte,
            })

    if any(r["risk_level"] == RISK_HIGH for r in at_risk):
        overall = RISK_HIGH
    elif any(r["risk_level"] == RISK_MEDIUM for r in at_risk):
        overall = RISK_MEDIUM
    else:
        overall = RISK_LOW

    recs = {
        RISK_HIGH:   "High assignment risk. Close or roll short legs immediately.",
        RISK_MEDIUM: "Moderate risk. Consider rolling or closing short legs.",
        RISK_LOW:    "No immediate assignment concern. Monitor as DTE shortens.",
    }
    return {
        "risk_level":      overall,
        "at_risk_legs":    at_risk,
        "dividend_driven": dividend_driven,
        "recommendation":  recs[overall],
    }


def automatic_exercise_check(legs: List[Leg], spot: float) -> List[Dict[str, Any]]:
    """
    Apply OCC automatic exercise rules at expiry.

    OCC Rule: options >= $0.01 ITM are automatically exercised unless the
    holder explicitly instructs otherwise.  Options < $0.01 ITM lapse.

    Returns one decision record per option leg.
    """
    results = []
    for lg in _option_legs(legs):
        k         = lg.strike or 0.0
        intrinsic = _intrinsic(lg, spot)

        if intrinsic >= OCC_AUTO_EXERCISE_THRESHOLD:
            decision = DECISION_EXERCISE if lg.side == SIDE_LONG else DECISION_ASSIGNED
        elif intrinsic > 0:
            decision = DECISION_AMBIGUOUS
        else:
            decision = DECISION_LAPSE

        results.append({
            "leg":           lg.option_symbol or f"{lg.side} {lg.asset_type}@{k}",
            "side":          lg.side,
            "asset_type":    lg.asset_type,
            "strike":        k,
            "spot":          spot,
            "intrinsic":     round(intrinsic, 4),
            "decision":      decision,
            "occ_threshold": OCC_AUTO_EXERCISE_THRESHOLD,
        })
    return results


def dividend_assignment_risk(
    legs: List[Leg],
    spot: float,
    ex_div_date: Optional[str] = None,
    annual_dividend: float = 0.0,
    days_to_ex_div: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Analyze dividend-driven early assignment risk for short calls.

    A rational long-call holder will exercise early before ex-div when the
    extrinsic value they sacrifice is less than the dividend they capture.

    Returns:
        risk_level       : LOW / MEDIUM / HIGH
        at_risk_legs     : legs facing dividend-driven assignment
        quarterly_dividend: per-quarter dividend estimate
        days_to_ex_div   : days until ex-dividend date
    """
    q_div = annual_dividend / 4.0 if annual_dividend > 0 else 0.0
    at_risk = []

    if annual_dividend > 0 and days_to_ex_div is not None:
        for lg in _option_legs(legs):
            if lg.asset_type != ASSET_CALL or lg.side != SIDE_SHORT:
                continue
            intrinsic = _intrinsic(lg, spot)
            if intrinsic <= 0:
                continue  # OTM calls not at risk
            extrinsic = _extrinsic(lg, spot)
            if extrinsic is None:
                continue
            if days_to_ex_div <= 3 and extrinsic < q_div:
                at_risk.append({
                    "leg":               lg.option_symbol or f"SHORT CALL@{lg.strike}",
                    "extrinsic":         round(extrinsic, 4),
                    "quarterly_dividend":round(q_div, 4),
                    "shortfall":         round(q_div - extrinsic, 4),
                    "days_to_ex_div":    days_to_ex_div,
                    "assessment":        "HIGH — holder will exercise to capture dividend",
                })

    if at_risk:
        overall = RISK_HIGH
    elif days_to_ex_div is not None and days_to_ex_div <= 5 and any(
        lg.asset_type == ASSET_CALL and lg.side == SIDE_SHORT for lg in legs
    ):
        overall = RISK_MEDIUM
    else:
        overall = RISK_LOW

    return {
        "risk_level":        overall,
        "at_risk_legs":      at_risk,
        "quarterly_dividend":round(q_div, 4),
        "days_to_ex_div":    days_to_ex_div,
        "ex_div_date":       ex_div_date,
    }


def pin_risk_analysis(legs: List[Leg], spot: float) -> Dict[str, Any]:
    """
    Analyze pin risk: risk that the underlying closes exactly at a short
    strike at expiry, leaving the position holder uncertain about assignment.

    Pin risk is highest when a short strike is within 0.5% of spot with
    DTE <= 2 — gamma is maximised and small price moves cause large delta swings.

    Returns:
        risk_level                    : LOW / MEDIUM / HIGH
        pin_events                    : list of at-risk short legs
        max_gamma_exposure_per_contract: highest gamma × 100 seen
        recommendation                : guidance string
    """
    pin_events = []
    max_gamma = 0.0

    for lg in _option_legs(legs):
        if lg.side != SIDE_SHORT:
            continue
        k             = lg.strike or 0.0
        dte           = lg.dte if lg.dte is not None else 999
        pct_from_spot = abs(k - spot) / spot if spot > 0 else 1.0
        gamma_pc      = abs(lg.gamma or 0.0) * 100
        max_gamma     = max(max_gamma, gamma_pc)

        if pct_from_spot < 0.005 and dte <= 2:
            level = RISK_HIGH
        elif pct_from_spot < 0.01 and dte <= 5:
            level = RISK_MEDIUM
        else:
            level = RISK_LOW

        pin_events.append({
            "leg":           lg.option_symbol or f"{lg.side} {lg.asset_type}@{k}",
            "strike":        k,
            "spot":          spot,
            "pct_from_spot": round(pct_from_spot * 100, 3),
            "dte":           dte,
            "gamma_per_contract": round(gamma_pc, 6),
            "risk_level":    level,
        })

    pin_events.sort(key=lambda x: x["pct_from_spot"])

    if any(p["risk_level"] == RISK_HIGH for p in pin_events):
        overall = RISK_HIGH
    elif any(p["risk_level"] == RISK_MEDIUM for p in pin_events):
        overall = RISK_MEDIUM
    else:
        overall = RISK_LOW

    recs = {
        RISK_HIGH:   "Close or hedge short positions at pinned strikes before expiry.",
        RISK_MEDIUM: "Monitor for pin risk as expiry approaches.",
        RISK_LOW:    "No significant pin risk detected.",
    }
    return {
        "risk_level":                      overall,
        "pin_events":                      pin_events,
        "max_gamma_exposure_per_contract": round(max_gamma, 6),
        "recommendation":                  recs[overall],
    }


def partial_assignment_impact(
    legs: List[Leg],
    spot: float,
    assigned_leg_index: int,
    assigned_quantity: int = 1,
) -> Dict[str, Any]:
    """
    Model impact of partial assignment on one leg of a multi-leg position.

    When one spread leg gets assigned, the remaining legs continue as a
    potentially naked or partially hedged position.

    Parameters:
        assigned_leg_index : index into legs of the assigned leg
        assigned_quantity  : contracts assigned (may be < full quantity)

    Returns:
        assigned_leg       : info + P&L on assigned leg
        residual_legs_count: count of remaining legs
        residual_has_naked_short: whether residual has uncovered shorts
        residual_risk      : LOW / HIGH
        residual_legs      : summary of remaining legs
    """
    if assigned_leg_index >= len(legs):
        return {"error": f"leg index {assigned_leg_index} out of range (len={len(legs)})"}

    al = legs[assigned_leg_index]
    residual = [lg for i, lg in enumerate(legs) if i != assigned_leg_index]

    k      = al.strike or 0.0
    prem   = al.mid or 0.0
    intrin = _intrinsic(al, spot)

    if al.side == SIDE_SHORT:
        pnl = (prem - intrin) * assigned_quantity * 100
    else:
        pnl = (intrin - prem) * assigned_quantity * 100

    # Check residual net exposure: use ratio-count, not strike comparison.
    # A bear call spread (SC@lower, LC@higher) is covered even though lc_k > sc_k.
    # Only net uncovered shorts (after all long offsets) signal naked risk.
    net_sc = sum(lg.ratio for lg in residual
                 if lg.asset_type == ASSET_CALL and lg.side == SIDE_SHORT
                ) - sum(lg.ratio for lg in residual
                 if lg.asset_type == ASSET_CALL and lg.side == SIDE_LONG
                ) - len([lg for lg in residual
                 if lg.asset_type == ASSET_STOCK and lg.side == SIDE_LONG])
    net_sp = sum(lg.ratio for lg in residual
                 if lg.asset_type == ASSET_PUT and lg.side == SIDE_SHORT
                ) - sum(lg.ratio for lg in residual
                 if lg.asset_type == ASSET_PUT and lg.side == SIDE_LONG
                ) - len([lg for lg in residual
                 if lg.asset_type == ASSET_STOCK and lg.side == SIDE_SHORT])
    has_naked = (net_sc > 0 or net_sp > 0)

    return {
        "assigned_leg": {
            "symbol":             al.option_symbol or f"{al.side} {al.asset_type}@{k}",
            "quantity_assigned":  assigned_quantity,
            "pnl_on_assigned_leg": round(pnl, 2),
        },
        "residual_legs_count":     len(residual),
        "residual_has_naked_short":has_naked,
        "residual_risk":           RISK_HIGH if has_naked else RISK_LOW,
        "residual_legs": [
            {
                "symbol":     lg.option_symbol or f"{lg.side} {lg.asset_type}@{lg.strike}",
                "side":       lg.side,
                "asset_type": lg.asset_type,
                "strike":     lg.strike,
            }
            for lg in residual
        ],
    }


def multi_leg_assignment_analysis(legs: List[Leg], spot: float) -> Dict[str, Any]:
    """
    Analyze assignment exposure across all short option legs simultaneously.

    Models worst-case where every at-risk short leg is simultaneously assigned.

    Returns:
        total_short_legs: count of short option legs
        legs_at_risk    : per-leg detail
        worst_case_pnl  : P&L if all at-risk legs are simultaneously assigned
        overall_risk    : LOW / MEDIUM / HIGH
    """
    short_legs = [(i, lg) for i, lg in enumerate(legs)
                  if lg.asset_type in (ASSET_CALL, ASSET_PUT) and lg.side == SIDE_SHORT]

    leg_analyses = []
    worst_pnl = 0.0

    for idx, lg in short_legs:
        k         = lg.strike or 0.0
        intrinsic = _intrinsic(lg, spot)
        extrinsic = _extrinsic(lg, spot)
        abs_delta = abs(lg.delta or 0.0)
        dte       = lg.dte if lg.dte is not None else 999
        prem      = lg.mid or 0.0

        if abs_delta >= 0.80 or (dte <= 1 and intrinsic > 0):
            likelihood = "HIGH"
        elif abs_delta >= 0.40 and dte <= 5:
            # ATM (delta≈0.50) short options at DTE≤5 carry real assignment risk
            likelihood = "MEDIUM"
        elif intrinsic > 0:
            likelihood = "LOW"
        else:
            likelihood = "NONE"

        # P&L if assigned: collected premium minus intrinsic lost
        pnl_if_assigned = (prem - intrinsic) * 100
        worst_pnl += pnl_if_assigned if likelihood != "NONE" else 0.0

        leg_analyses.append({
            "leg_index":             idx,
            "leg":                   lg.option_symbol or f"{lg.side} {lg.asset_type}@{k}",
            "assignment_likelihood": likelihood,
            "intrinsic":             round(intrinsic, 4),
            "extrinsic":             round(extrinsic, 4) if extrinsic is not None else None,
            "abs_delta":             round(abs_delta, 3),
            "dte":                   dte,
            "pnl_if_assigned":       round(pnl_if_assigned, 2),
        })

    any_high   = any(l["assignment_likelihood"] == "HIGH"   for l in leg_analyses)
    any_medium = any(l["assignment_likelihood"] == "MEDIUM" for l in leg_analyses)

    return {
        "total_short_legs":            len(short_legs),
        "legs_at_risk":                leg_analyses,
        "worst_case_simultaneous_pnl": round(worst_pnl, 2),
        "overall_risk":                RISK_HIGH if any_high else (
                                       RISK_MEDIUM if any_medium else RISK_LOW),
    }


def expiration_outcome(legs: List[Leg], spot: float) -> Dict[str, Any]:
    """
    Simulate the full expiration outcome for all legs.

    Applies OCC auto-exercise rules (>= $0.01 ITM = exercise/assigned,
    otherwise lapse) and computes per-leg P&L, net settlement, and any
    stock positions created by exercise or assignment.

    Returns:
        spot_at_expiry : spot price used
        leg_outcomes   : per-leg decision, P&L, shares created
        net_pnl        : total strategy P&L at expiry
        net_stock_position: shares created from exercise/assignment
        has_stock_residual: True if any stock position created
    """
    leg_outcomes = []
    net_pnl      = 0.0
    stock_delta  = 0

    for lg in _option_legs(legs):
        k        = lg.strike or 0.0
        prem     = lg.mid or 0.0
        intrinsic= _intrinsic(lg, spot)

        if intrinsic >= OCC_AUTO_EXERCISE_THRESHOLD:
            if lg.side == SIDE_LONG:
                decision = DECISION_EXERCISE
                pnl      = (intrinsic - prem) * 100
                shares   = +100 if lg.asset_type == ASSET_CALL else -100
            else:
                decision = DECISION_ASSIGNED
                pnl      = (prem - intrinsic) * 100
                shares   = -100 if lg.asset_type == ASSET_CALL else +100
        elif intrinsic > 0:
            decision = DECISION_AMBIGUOUS
            pnl      = -prem * 100 if lg.side == SIDE_LONG else prem * 100
            shares   = 0
        else:
            decision = DECISION_LAPSE
            pnl      = -prem * 100 if lg.side == SIDE_LONG else prem * 100
            shares   = 0

        net_pnl     += pnl
        stock_delta += shares
        leg_outcomes.append({
            "leg":                lg.option_symbol or f"{lg.side} {lg.asset_type}@{k}",
            "side":               lg.side,
            "decision":           decision,
            "intrinsic_at_expiry":round(intrinsic, 4),
            "premium":            round(prem, 4),
            "pnl":                round(pnl, 2),
            "shares_created":     shares,
        })

    for lg in [l for l in legs if l.asset_type == ASSET_STOCK]:
        entry  = lg.strike or spot
        mult   = 1 if lg.side == SIDE_LONG else -1
        pnl    = (spot - entry) * getattr(lg, "quantity", 1) * mult
        net_pnl += pnl
        leg_outcomes.append({
            "leg":      f"{lg.side} STOCK",
            "side":     lg.side,
            "decision": "SETTLED",
            "pnl":      round(pnl, 2),
            "shares_created": 0,
        })

    return {
        "spot_at_expiry":    spot,
        "leg_outcomes":      leg_outcomes,
        "net_pnl":           round(net_pnl, 2),
        "net_stock_position":stock_delta,
        "has_stock_residual":stock_delta != 0,
    }


def exercise_simulation(
    legs: List[Leg],
    spot: float,
    exercise_decisions: Dict[int, str],
) -> Dict[str, Any]:
    """
    Apply specific exercise/lapse decisions (overriding OCC defaults) and
    compute resulting P&L and stock position.

    exercise_decisions: {leg_index → EXERCISE | LAPSE}
      Only long legs can be directed; short legs are modeled as assigned
      whenever the counterpart intrinsic >= OCC threshold.

    Returns:
        decisions_applied : the input dict
        leg_results       : per-leg action and P&L
        total_pnl         : net P&L across all legs
        net_stock_delta   : shares created from exercise/assignment
        has_stock_residual: True if any stock position created
    """
    option_legs = [(i, lg) for i, lg in enumerate(legs)
                   if lg.asset_type in (ASSET_CALL, ASSET_PUT)]
    stock_legs  = [(i, lg) for i, lg in enumerate(legs)
                   if lg.asset_type == ASSET_STOCK]

    results     = []
    total_pnl   = 0.0
    stock_delta = 0

    for i, lg in option_legs:
        k        = lg.strike or 0.0
        prem     = lg.mid or 0.0
        intrinsic= _intrinsic(lg, spot)
        directed = exercise_decisions.get(i)

        if lg.side == SIDE_LONG:
            if directed == DECISION_EXERCISE:
                pnl    = (intrinsic - prem) * 100
                shares = +100 if lg.asset_type == ASSET_CALL else -100
                action = DECISION_EXERCISE
            elif directed == DECISION_LAPSE:
                pnl    = -prem * 100
                shares = 0
                action = DECISION_LAPSE
            else:
                # Apply OCC default
                if intrinsic >= OCC_AUTO_EXERCISE_THRESHOLD:
                    pnl    = (intrinsic - prem) * 100
                    shares = +100 if lg.asset_type == ASSET_CALL else -100
                    action = "AUTO_EXERCISED"
                else:
                    pnl    = -prem * 100
                    shares = 0
                    action = "AUTO_LAPSED"
        else:  # SHORT — assigned if ITM
            if intrinsic >= OCC_AUTO_EXERCISE_THRESHOLD:
                pnl    = (prem - intrinsic) * 100
                shares = -100 if lg.asset_type == ASSET_CALL else +100
                action = DECISION_ASSIGNED
            else:
                pnl    = prem * 100
                shares = 0
                action = DECISION_NOT_ASSIGNED

        total_pnl   += pnl
        stock_delta += shares
        results.append({
            "leg_index":    i,
            "leg":          lg.option_symbol or f"{lg.side} {lg.asset_type}@{k}",
            "action":       action,
            "pnl":          round(pnl, 2),
            "shares_created": shares,
        })

    for i, lg in stock_legs:
        entry = lg.strike or spot
        mult  = 1 if lg.side == SIDE_LONG else -1
        pnl   = (spot - entry) * getattr(lg, "quantity", 1) * mult
        total_pnl += pnl
        results.append({
            "leg_index":      i,
            "leg":            f"{lg.side} STOCK",
            "action":         "SETTLED",
            "pnl":            round(pnl, 2),
            "shares_created": 0,
        })

    return {
        "decisions_applied": exercise_decisions,
        "leg_results":       results,
        "total_pnl":         round(total_pnl, 2),
        "net_stock_delta":   stock_delta,
        "has_stock_residual":stock_delta != 0,
    }
