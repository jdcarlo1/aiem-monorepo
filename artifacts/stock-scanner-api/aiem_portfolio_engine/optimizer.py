"""
aiem_portfolio_engine/optimizer.py — S8: Portfolio Optimization Engine.

Evaluates the candidate as an addition to the complete existing portfolio.
Compares: existing portfolio (NO TRADE) vs existing + candidate.

IMPLEMENTED:
- Single-candidate-vs-cash comparison
- Risk-adjusted return comparison
- Capital preservation scoring
- Diversification scoring
- Greek balance assessment
- NO TRADE is always a valid optimized outcome

NOT_IMPLEMENTED v1:
- Candidate combination optimization (combinatorial — deferred to v2)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .snapshot import PortfolioSnapshot
from .greeks import PortfolioGreeks
from .limits import ConcentrationResult, RiskBudget
from .correlation import CorrelationResult
from .stress import StressScenario, worst_stress_loss
from .valuation import LiquidityValuation
from .config import (
    PORTFOLIO_CAPITAL, DAILY_LOSS_LIMIT, STRESS_TEST_LOSS_LIMIT,
    MAX_PORTFOLIO_DELTA, MAX_PORTFOLIO_VEGA, NOT_IMPLEMENTED_V1,
)


# ── Decision constants ────────────────────────────────────────────────────────
APPROVE               = "APPROVE"
APPROVE_REDUCED_SIZE  = "APPROVE_REDUCED_SIZE"
SUBSTITUTE            = "SUBSTITUTE_LOWER_RISK"
DEFER                 = "DEFER"
REJECT                = "REJECT"
NO_TRADE              = "NO_TRADE"


@dataclass
class OptimizationResult:
    decision:             str
    requested_size:       int
    approved_size:        int
    reasons:              List[str]
    no_trade_score:       float     # utility score of keeping cash (baseline)
    candidate_score:      float     # utility score of adding candidate
    score_delta:          float     # candidate_score - no_trade_score
    not_implemented_items: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "requested_size": self.requested_size,
            "approved_size": self.approved_size,
            "reasons": self.reasons,
            "no_trade_score": round(self.no_trade_score, 4),
            "candidate_score": round(self.candidate_score, 4),
            "score_delta": round(self.score_delta, 4),
            "not_implemented_items": self.not_implemented_items,
        }


def _greek_balance_score(g: PortfolioGreeks) -> float:
    """
    Score how balanced the portfolio Greeks are (0=unbalanced, 1=balanced).
    Penalizes extreme directional or vega exposure.
    """
    delta_score = max(0.0, 1.0 - abs(g.total_delta) / max(MAX_PORTFOLIO_DELTA, 1))
    vega_score  = max(0.0, 1.0 - abs(g.vega)        / max(MAX_PORTFOLIO_VEGA, 1))
    return round((delta_score + vega_score) / 2.0, 4)


def _diversification_score(snapshot: PortfolioSnapshot, candidate_ticker: str) -> float:
    """
    Score diversification benefit of adding the candidate.
    Higher if candidate is in a new ticker/sector not already in portfolio.
    """
    existing_tickers = {p.ticker for p in snapshot.positions}
    if candidate_ticker not in existing_tickers:
        return 1.0
    count = sum(1 for p in snapshot.positions if p.ticker == candidate_ticker)
    return max(0.0, 1.0 - count * 0.25)


def _compute_utility(
    snapshot: PortfolioSnapshot,
    greeks: PortfolioGreeks,
    stress_scenarios: List[StressScenario],
    valuation: LiquidityValuation,
    conc: ConcentrationResult,
    candidate_ev: float = 0.0,
    candidate_pop: float = 0.0,
    candidate_ticker: str = "",
    is_candidate: bool = False,
) -> float:
    """
    Compute portfolio utility score (higher = better).
    Weights: EV (0.25), risk-adj return (0.20), capital preservation (0.20),
             diversification (0.15), greek balance (0.10), liquidity (0.10)
    """
    # Expected value component
    ev_score = min(1.0, max(0.0, (candidate_ev / max(PORTFOLIO_CAPITAL * 0.001, 1))))

    # Risk-adjusted: EV per unit of stress loss
    wsl      = abs(worst_stress_loss(stress_scenarios)) or 1.0
    risk_adj = min(1.0, max(0.0, candidate_ev / wsl)) if is_candidate else 0.5

    # Capital preservation: fraction of capital not at risk
    committed_frac = snapshot.committed_capital / max(PORTFOLIO_CAPITAL, 1)
    cap_preserve   = max(0.0, 1.0 - committed_frac)

    # Diversification
    div_score = _diversification_score(snapshot, candidate_ticker) if is_candidate else 0.5

    # Greek balance
    greek_score = _greek_balance_score(greeks)

    # Liquidity
    liq_score = max(0.0, 1.0 - valuation.partial_fill_risk_score)

    # Breach penalty
    breach_penalty = len(conc.breaches) * 0.10

    score = (
        0.25 * ev_score
        + 0.20 * risk_adj
        + 0.20 * cap_preserve
        + 0.15 * div_score
        + 0.10 * greek_score
        + 0.10 * liq_score
        - breach_penalty
    )
    return round(max(0.0, min(1.0, score)), 4)


def optimize_portfolio(
    snapshot: PortfolioSnapshot,
    candidate_ticker: str,
    candidate_strategy_name: str,
    candidate_ev: float,
    candidate_pop: float,
    candidate_capital: float,
    requested_contracts: int,
    greeks_before: PortfolioGreeks,
    greeks_after: PortfolioGreeks,
    concentration: ConcentrationResult,
    correlation: CorrelationResult,
    stress_before: List[StressScenario],
    stress_after: List[StressScenario],
    valuation: LiquidityValuation,
    risk_budget: RiskBudget,
) -> OptimizationResult:
    """
    Determine final portfolio-aware size and decision for the candidate.
    """
    reasons: List[str] = []
    hard_blocks: List[str] = []

    # ── Hard limit enforcement ────────────────────────────────────────────────
    conc_hard = [b for b in concentration.breaches
                 if b.current_value > b.limit_value * 1.1]
    for b in conc_hard:
        hard_blocks.append(f"{b.limit_name}: {b.details}")

    if risk_budget.breaches:
        for b in risk_budget.breaches:
            hard_blocks.append(f"risk_budget.{b.limit_name}: {b.details}")

    if valuation.liquidity_limit_breach:
        hard_blocks.append(f"liquidity: {valuation.breach_details}")

    if correlation.action == "REJECT":
        hard_blocks.append(f"correlation: duplicate risk score={correlation.duplicate_risk_score:.2f}")

    stress_hard = [s for s in stress_after if s.limit_breach]
    if stress_hard:
        hard_blocks.append(
            f"stress: {len(stress_hard)} scenarios breach limit; "
            f"worst={min(s.pl_combined for s in stress_hard):.0f}"
        )

    if not snapshot.reconciled:
        hard_blocks.append(f"snapshot: not reconciled — {snapshot.reconcile_error}")

    # ── Compute utility scores ────────────────────────────────────────────────
    no_trade_score = _compute_utility(
        snapshot, greeks_before, stress_before, valuation, concentration,
        candidate_ev=0.0, candidate_pop=0.5, candidate_ticker="", is_candidate=False,
    )
    candidate_score = _compute_utility(
        snapshot, greeks_after, stress_after, valuation, concentration,
        candidate_ev=candidate_ev, candidate_pop=candidate_pop,
        candidate_ticker=candidate_ticker, is_candidate=True,
    )
    score_delta = candidate_score - no_trade_score

    # ── Decision tree ─────────────────────────────────────────────────────────
    if hard_blocks:
        decision      = REJECT
        approved_size = 0
        reasons.extend([f"HARD_BLOCK: {b}" for b in hard_blocks])
        reasons.append(f"utility: candidate={candidate_score:.3f} vs no_trade={no_trade_score:.3f}")

    elif score_delta < -0.05:
        # Keeping cash is meaningfully better
        decision      = NO_TRADE
        approved_size = 0
        reasons.append(
            f"NO_TRADE: keeping cash has higher utility "
            f"(no_trade={no_trade_score:.3f} vs candidate={candidate_score:.3f})"
        )

    elif (
        correlation.action == "REDUCE"
        and len(concentration.breaches) >= 1
        and candidate_ev > 1.0
    ):
        # Positive EV but correlation + concentration overlap → suggest alternative structure
        approved_size = max(1, requested_contracts // 2)
        decision      = SUBSTITUTE
        reasons.append(
            f"SUBSTITUTE_LOWER_RISK: EV={candidate_ev:.2f} positive but "
            f"correlation={correlation.action} + {len(concentration.breaches)} "
            f"concentration breach(es); suggest alternative lower-risk structure"
        )
        reasons.append(f"utility delta: {score_delta:+.3f}")

    elif correlation.action == "REDUCE" or any(
        b for b in concentration.breaches if b.current_value > b.limit_value
    ):
        # Reduce size: divide by 2 (max 50% reduction to respect risk budget)
        approved_size = max(1, requested_contracts // 2)
        decision      = APPROVE_REDUCED_SIZE
        reasons.append(
            f"REDUCED from {requested_contracts} to {approved_size} contracts "
            f"due to concentration/correlation soft limits"
        )
        reasons.append(f"utility delta: {score_delta:+.3f}")

    elif candidate_score > 0 and score_delta >= 0:
        approved_size = requested_contracts
        decision      = APPROVE
        reasons.append(
            f"APPROVE: candidate improves portfolio utility "
            f"(delta={score_delta:+.3f})"
        )

    else:
        # Marginal: defer
        approved_size = 0
        decision      = DEFER
        reasons.append(
            f"DEFER: marginal improvement (delta={score_delta:+.3f}); "
            f"no hard breach but utility gain insufficient"
        )

    return OptimizationResult(
        decision              = decision,
        requested_size        = requested_contracts,
        approved_size         = approved_size,
        reasons               = reasons,
        no_trade_score        = no_trade_score,
        candidate_score       = candidate_score,
        score_delta           = score_delta,
        not_implemented_items = [NOT_IMPLEMENTED_V1[2]],  # combination optimization
    )
