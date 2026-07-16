"""
selector.py — Strategy selection with NO_TRADE baseline comparison.

Rules:
1. Evaluate ALL eligible strategies and NO_TRADE.
2. Select the highest-scoring strategy ONLY if it materially exceeds NO_TRADE.
3. If multiple strategies tie, prefer simpler (fewer legs) + more defined risk.
4. Analysis-only strategies are scored but never selected for execution.
5. Never select a strategy that did not pass eligibility.
"""
from __future__ import annotations
from typing import List, Optional, Tuple, Dict, Any
from .scoring import compute_capital_compounding_score, no_trade_score
from .legs import Leg, MODE_ANALYSIS_ONLY, RISK_UNDEFINED
from .config import NO_TRADE_SCORE, PORTFOLIO_CAPITAL


# Minimum margin a strategy must beat NO_TRADE to be selected
MIN_EDGE_OVER_NO_TRADE = 0.05


class EvaluationResult:
    """Full evaluation for one candidate strategy."""
    def __init__(
        self,
        strategy_name:     str,
        strategy_family:   str,
        strategy_fingerprint: str,
        risk_class:        str,
        execution_mode:    str,
        eligible:          bool,
        rejection_reasons: List[str],
        legs:              List[Leg],
        payoff_info:       Dict[str, Any],
        probability_info:  Dict[str, Any],
        pricing_info:      Dict[str, Any],
        greeks_info:       Dict[str, Any],
        score_components:  Dict[str, float],
        capital_compounding_score: float,
        iv_rank:           Optional[float] = None,
    ):
        self.strategy_name      = strategy_name
        self.strategy_family    = strategy_family
        self.strategy_fingerprint = strategy_fingerprint
        self.risk_class         = risk_class
        self.execution_mode     = execution_mode
        self.eligible           = eligible
        self.rejection_reasons  = rejection_reasons
        self.legs               = legs
        self.payoff_info        = payoff_info
        self.probability_info   = probability_info
        self.pricing_info       = pricing_info
        self.greeks_info        = greeks_info
        self.score_components   = score_components
        self.capital_compounding_score = capital_compounding_score
        self.iv_rank            = iv_rank

    def is_selectable(self) -> bool:
        """Can this strategy actually be paper-traded?"""
        return (
            self.eligible
            and self.execution_mode != MODE_ANALYSIS_ONLY
            and self.risk_class != RISK_UNDEFINED
            and self.payoff_info.get("max_loss") is not None
            and not self.payoff_info.get("is_undefined_risk", False)
        )


class SelectionResult:
    """Final selection decision for a run."""
    def __init__(
        self,
        decision:          str,                         # TRADE | NO_TRADE | INSUFFICIENT_DATA
        selected:          Optional[EvaluationResult],
        runner_up:         Optional[EvaluationResult],
        no_trade_score_:   float,
        all_evaluations:   List[EvaluationResult],
        reason:            str,
    ):
        self.decision        = decision
        self.selected        = selected
        self.runner_up       = runner_up
        self.no_trade_score  = no_trade_score_
        self.all_evaluations = all_evaluations
        self.reason          = reason

    @property
    def strategies_evaluated(self) -> int:
        return len(self.all_evaluations)

    @property
    def strategies_rejected(self) -> int:
        return sum(1 for e in self.all_evaluations if not e.eligible)


def select(
    evaluations: List[EvaluationResult],
    thesis: str,
    market_regime: str,
    iv_rank: Optional[float],
    existing_families: Optional[List[str]] = None,
) -> SelectionResult:
    """
    Select the best strategy from a list of EvaluationResults.
    Always includes NO_TRADE as a baseline.
    """
    if not evaluations:
        return SelectionResult(
            decision="INSUFFICIENT_DATA",
            selected=None,
            runner_up=None,
            no_trade_score_=NO_TRADE_SCORE,
            all_evaluations=[],
            reason="No strategies evaluated",
        )

    nt_score = no_trade_score(thesis, market_regime, iv_rank)

    # Filter to selectable strategies (eligible, autonomous, defined-risk)
    selectable = [e for e in evaluations if e.is_selectable()]

    if not selectable:
        return SelectionResult(
            decision="NO_TRADE",
            selected=None,
            runner_up=None,
            no_trade_score_=nt_score,
            all_evaluations=evaluations,
            reason="No eligible autonomous defined-risk strategies found",
        )

    # Sort by score descending; tie-break by fewer legs, then defined risk
    def sort_key(e: EvaluationResult):
        n_legs = len(e.legs)
        dr_bonus = 0.01 if e.risk_class == "DEFINED_RISK" else 0.0
        return (-(e.capital_compounding_score + dr_bonus), n_legs)

    selectable_sorted = sorted(selectable, key=sort_key)
    best    = selectable_sorted[0]
    runner  = selectable_sorted[1] if len(selectable_sorted) > 1 else None

    # Must exceed NO_TRADE by MIN_EDGE_OVER_NO_TRADE
    if best.capital_compounding_score < nt_score + MIN_EDGE_OVER_NO_TRADE:
        return SelectionResult(
            decision="NO_TRADE",
            selected=None,
            runner_up=best,
            no_trade_score_=nt_score,
            all_evaluations=evaluations,
            reason=(
                f"Best strategy score {best.capital_compounding_score:.3f} does not "
                f"exceed NO_TRADE threshold {nt_score + MIN_EDGE_OVER_NO_TRADE:.3f}"
            ),
        )

    return SelectionResult(
        decision="TRADE",
        selected=best,
        runner_up=runner,
        no_trade_score_=nt_score,
        all_evaluations=evaluations,
        reason=(
            f"Selected '{best.strategy_name}' score={best.capital_compounding_score:.3f} "
            f"vs NO_TRADE={nt_score:.3f} (+{best.capital_compounding_score-nt_score:.3f})"
        ),
    )


def rank_all(
    evaluations: List[EvaluationResult],
) -> List[Tuple[int, EvaluationResult, str]]:
    """
    Full ranking of all strategies + NO_TRADE for reporting.
    Returns list of (rank, evaluation, notes) tuples.
    """
    scored = [(e.capital_compounding_score, len(e.legs), e) for e in evaluations]
    scored.sort(key=lambda x: (-x[0], x[1]))  # high score first, fewer legs preferred

    result = []
    for rank, (score, n_legs, e) in enumerate(scored, 1):
        notes = []
        if not e.eligible:
            notes.append(f"INELIGIBLE: {'; '.join(e.rejection_reasons[:2])}")
        if e.execution_mode == MODE_ANALYSIS_ONLY:
            notes.append("ANALYSIS_ONLY")
        if e.risk_class == RISK_UNDEFINED:
            notes.append("UNDEFINED_RISK")
        result.append((rank, e, " | ".join(notes) if notes else ""))

    return result


def evaluation_summary(eval_: EvaluationResult) -> Dict[str, Any]:
    """Flat dict suitable for DB insertion into ase_strategy_evaluations."""
    p   = eval_.payoff_info
    pr  = eval_.probability_info
    px  = eval_.pricing_info
    g   = eval_.greeks_info
    sc  = eval_.score_components

    return {
        "strategy_name":       eval_.strategy_name,
        "strategy_family":     eval_.strategy_family,
        "strategy_fingerprint":eval_.strategy_fingerprint,
        "risk_class":          eval_.risk_class,
        "execution_mode":      eval_.execution_mode,
        "eligible":            eval_.eligible,
        "rejection_reasons":   eval_.rejection_reasons or [],
        "net_debit_credit":    p.get("net_cost"),
        "mid_price":           px.get("mid"),
        "conservative_fill":   px.get("conservative_fill"),
        "slippage":            px.get("slippage"),
        "commission":          px.get("commission"),
        "max_profit":          p.get("max_profit"),
        "max_loss":            p.get("max_loss"),
        "breakevens":          p.get("breakevens", []),
        "pop":                 pr.get("pop"),
        "pop_touch":           pr.get("pop_touch"),
        "pop_max_profit":      pr.get("pop_max_profit"),
        "pop_max_loss":        pr.get("pop_max_loss"),
        "ev_before_costs":     px.get("ev_before_costs"),
        "ev_after_costs":      px.get("ev_after_costs"),
        "return_on_capital":   px.get("return_on_capital"),
        "return_on_risk":      px.get("return_on_risk"),
        "capital_at_risk":     px.get("capital_at_risk"),
        "buying_power":        px.get("buying_power"),
        "reward_risk":         px.get("reward_risk"),
        "delta":               g.get("delta"),
        "gamma":               g.get("gamma"),
        "theta":               g.get("theta"),
        "vega":                g.get("vega"),
        "rho":                 g.get("rho"),
        "charm":               g.get("charm"),
        "vanna":               g.get("vanna"),
        "vomma":               g.get("vomma"),
        "liquidity_score":     px.get("liquidity_score"),
        "score_pop":           sc.get("score_pop"),
        "score_ev":            sc.get("score_ev"),
        "score_capital_pres":  sc.get("score_capital_pres"),
        "score_defined_risk":  sc.get("score_defined_risk"),
        "score_cap_efficiency":sc.get("score_cap_efficiency"),
        "score_liquidity":     sc.get("score_liquidity"),
        "score_thesis_fit":    sc.get("score_thesis_fit"),
        "score_regime_fit":    sc.get("score_regime_fit"),
        "score_vol_fit":       sc.get("score_vol_fit"),
        "score_diversification":sc.get("score_diversification"),
        "penalty_total":       sc.get("penalty_total"),
        "capital_compounding_score": eval_.capital_compounding_score,
        "legs_json":           [lg.to_dict() for lg in eval_.legs],
        "payoff_grid":         p.get("payoff_grid"),
        "iv_rank":             eval_.iv_rank,
    }
