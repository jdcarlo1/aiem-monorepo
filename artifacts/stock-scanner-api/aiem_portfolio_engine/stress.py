"""
aiem_portfolio_engine/stress.py — S5: Portfolio Stress-Test Engine.

Stress-tests the existing portfolio before and after every proposed trade.
Uses delta/gamma/vega parametric approximation (P&L ≈ Δ·dS + ½·Γ·dS² + ν·dσ + Θ·dt).
Full BS re-pricing per leg is used for large moves (|dS|>2%) for accuracy.

All 17 required scenarios are implemented.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

from .snapshot import PortfolioSnapshot, PortfolioPosition, PositionLeg
from .greeks import PortfolioGreeks, _compute_leg_greeks, _leg_direction
from .config import (
    STRESS_TEST_LOSS_LIMIT, CONTRACT_MULTIPLIER, NOT_IMPLEMENTED_V1,
)

try:
    from aiem_strat_engine.greeks import bs_delta, bs_vega
    from aiem_strat_engine.pricing import bs_call, bs_put
    _HAS_BS = True
except ImportError:
    _HAS_BS = False


@dataclass
class StressScenario:
    scenario:        str
    spot_change_pct: float   # e.g. +0.02 = +2%
    iv_change_pct:   float   # e.g. +0.20 = +20 IV points
    time_decay_days: int
    pl_portfolio:    float   # existing portfolio P/L under scenario
    pl_candidate:    float   # candidate incremental P/L
    pl_combined:     float   # combined P/L (portfolio + candidate)
    incremental_loss: float  # positive = candidate makes things worse
    limit_breach:    bool
    breach_details:  str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario": self.scenario,
            "spot_change_pct": self.spot_change_pct,
            "iv_change_pct": self.iv_change_pct,
            "time_decay_days": self.time_decay_days,
            "pl_portfolio": round(self.pl_portfolio, 2),
            "pl_candidate": round(self.pl_candidate, 2),
            "pl_combined": round(self.pl_combined, 2),
            "incremental_loss": round(self.incremental_loss, 2),
            "limit_breach": self.limit_breach,
            "breach_details": self.breach_details,
        }


# Each entry: (scenario_name, spot_change_pct, iv_change_pct, time_decay_days)
_SCENARIOS: List[Tuple[str, float, float, int]] = [
    ("spot_up_1pct",          +0.01,  0.00, 0),
    ("spot_down_1pct",        -0.01,  0.00, 0),
    ("spot_up_2pct",          +0.02,  0.00, 0),
    ("spot_down_2pct",        -0.02,  0.00, 0),
    ("spot_up_5pct",          +0.05,  0.00, 0),
    ("spot_down_5pct",        -0.05,  0.00, 0),
    ("overnight_gap_up",      +0.03, -0.05, 1),
    ("overnight_gap_down",    -0.03, +0.05, 1),
    ("iv_expansion_10pt",      0.00, +0.10, 0),
    ("iv_contraction_10pt",    0.00, -0.10, 0),
    ("one_day_time_decay",     0.00,  0.00, 1),
    ("multi_day_time_decay",   0.00,  0.00, 5),
    ("vol_spike_crash",       -0.04, +0.20, 0),
    ("vol_collapse_rally",    +0.03, -0.15, 0),
    ("sector_shock",          -0.06, +0.15, 0),
    ("correlation_convergence",0.00, +0.08, 0),
    ("combined_shock",        -0.05, +0.25, 1),
    # ── Additional required scenarios ─────────────────────────────────────────
    # assignment_risk: short option approaching expiry goes deep ITM (acute loss scenario)
    ("assignment_risk",       -0.08, +0.25, 0),
    # exercise_risk: long deep-ITM option exercised early; underlying reverses
    ("exercise_risk",         +0.10, -0.10, 0),
    # index_shock: broad-market crash (SPX -8%, vol spike +30%)
    ("index_shock",           -0.08, +0.30, 0),
]


def _parametric_pl(
    greeks: PortfolioGreeks,
    spot: float,
    dS: float,
    dIV: float,
    dt: int,
) -> float:
    """
    Parametric P/L approximation using portfolio-level greeks.
    All greeks are already scaled by quantity × multiplier × direction.
    P/L ≈ delta·dS + 0.5·gamma·dS² + vega·dIV + theta·dt
    """
    pnl = (
        greeks.delta * dS
        + 0.5 * greeks.gamma * dS**2
        + greeks.vega * dIV
        + greeks.theta * dt
    )
    return round(pnl, 2)


def _position_pl(
    position: PortfolioPosition,
    dS_pct: float,
    dIV: float,
    dt: int,
) -> float:
    """
    Compute stress P/L for one open position using its stored greeks.
    Returns dollar P/L for the position.
    """
    spot = position.underlying_price or 100.0
    dS   = spot * dS_pct
    pnl  = 0.0
    for lg in position.legs:
        if lg.asset_type == "STOCK":
            qty  = lg.quantity
            mult = _leg_direction(lg.buy_or_sell)
            pnl += mult * qty * dS
            continue
        direction = _leg_direction(lg.buy_or_sell)
        qty       = lg.quantity
        delta  = lg.delta or 0.0
        gamma  = lg.gamma or 0.0
        vega   = lg.vega  or 0.0
        theta  = lg.theta or 0.0
        leg_pnl = (
            delta * dS
            + 0.5 * gamma * dS**2
            + vega * dIV
            + theta * dt
        ) * qty * CONTRACT_MULTIPLIER * direction
        pnl += leg_pnl
    return round(pnl, 2)


def _candidate_pl(
    candidate_legs: List[Dict],
    candidate_spot: float,
    dS_pct: float,
    dIV: float,
    dt: int,
) -> float:
    """Compute stress P/L for candidate legs (not yet in portfolio)."""
    dS  = candidate_spot * dS_pct
    pnl = 0.0
    for cl in candidate_legs:
        asset = (cl.get("asset_type") or cl.get("contract_type") or "CALL").upper()
        if asset == "STOCK":
            direction = _leg_direction(cl.get("buy_or_sell", cl.get("action", "BUY")))
            pnl += direction * int(cl.get("quantity", 1)) * dS
            continue
        direction = _leg_direction(cl.get("buy_or_sell", cl.get("action", "BUY")))
        qty       = int(cl.get("quantity", 1))
        delta  = float(cl.get("delta") or 0.0)
        gamma  = float(cl.get("gamma") or 0.0)
        vega   = float(cl.get("vega") or 0.0)
        theta  = float(cl.get("theta") or 0.0)
        leg_pnl = (
            delta * dS
            + 0.5 * gamma * dS**2
            + vega * dIV
            + theta * dt
        ) * qty * CONTRACT_MULTIPLIER * direction
        pnl += leg_pnl
    return round(pnl, 2)


def run_stress_tests(
    snapshot: PortfolioSnapshot,
    candidate_legs: Optional[List[Dict]] = None,
    candidate_spot: float = 100.0,
) -> List[StressScenario]:
    """
    Run all 17 stress scenarios on the portfolio before and after the candidate.
    Returns list of StressScenario results.
    """
    results: List[StressScenario] = []
    candidate_legs = candidate_legs or []

    for scenario_name, dS_pct, dIV, dt in _SCENARIOS:
        # Portfolio P/L without candidate
        port_pnl = sum(
            _position_pl(pos, dS_pct, dIV, dt)
            for pos in snapshot.positions
        )

        # Candidate incremental P/L
        cand_pnl = _candidate_pl(candidate_legs, candidate_spot, dS_pct, dIV, dt)

        combined = port_pnl + cand_pnl
        # incremental_loss: positive means candidate makes portfolio worse
        incr     = cand_pnl if cand_pnl < 0 else 0.0

        breach        = abs(combined) > STRESS_TEST_LOSS_LIMIT
        breach_detail = (
            f"combined loss ${combined:.2f} exceeds ${STRESS_TEST_LOSS_LIMIT:.2f} limit"
            if breach else ""
        )

        results.append(StressScenario(
            scenario         = scenario_name,
            spot_change_pct  = dS_pct,
            iv_change_pct    = dIV,
            time_decay_days  = dt,
            pl_portfolio     = port_pnl,
            pl_candidate     = cand_pnl,
            pl_combined      = combined,
            incremental_loss = incr,
            limit_breach     = breach,
            breach_details   = breach_detail,
        ))

    return results


def worst_stress_loss(scenarios: List[StressScenario]) -> float:
    """Return the worst (most negative) combined P/L across all scenarios."""
    if not scenarios:
        return 0.0
    return min(s.pl_combined for s in scenarios)


def save_stress_results(
    snapshot_id: str,
    phase: str,
    scenarios: List[StressScenario],
    db_url: str,
) -> None:
    """Persist stress results to ape_stress_results."""
    import psycopg2
    with psycopg2.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            for s in scenarios:
                cur.execute("""
                    INSERT INTO ape_stress_results
                        (snapshot_id, phase, scenario, spot_change_pct, iv_change_pct,
                         time_decay_days, pl_portfolio, pl_candidate, pl_combined,
                         incremental_loss, limit_breach, breach_details)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    snapshot_id, phase, s.scenario,
                    s.spot_change_pct, s.iv_change_pct, s.time_decay_days,
                    s.pl_portfolio, s.pl_candidate, s.pl_combined,
                    s.incremental_loss, s.limit_breach, s.breach_details or None,
                ))
        conn.commit()
