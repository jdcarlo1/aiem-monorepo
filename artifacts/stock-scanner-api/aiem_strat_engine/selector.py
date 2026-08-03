"""
selector.py — Strategy selection with NO_TRADE baseline comparison.

Rules:
1. Pre-filter the catalog by direction, volatility regime, DTE, event context,
   and defined-risk status before scoring (Phase 5 §7 compatibility gate).
2. Evaluate ALL compatible strategies and NO_TRADE.
3. Select the highest-scoring strategy ONLY if it materially exceeds NO_TRADE.
4. If multiple strategies tie, prefer simpler (fewer legs) + more defined risk.
5. Analysis-only strategies are scored but never selected for execution.
6. Never select a strategy that did not pass eligibility.
"""
from __future__ import annotations
from typing import List, Optional, Tuple, Dict, Any, TYPE_CHECKING
from .scoring import compute_capital_compounding_score, no_trade_score
from .legs import Leg, MODE_ANALYSIS_ONLY, MODE_AUTONOMOUS, RISK_UNDEFINED, RISK_DEFINED, RISK_LIMITED
from .config import NO_TRADE_SCORE, PORTFOLIO_CAPITAL

if TYPE_CHECKING:
    from .catalog import StrategySpec


# Minimum margin a strategy must beat NO_TRADE to be selected
MIN_EDGE_OVER_NO_TRADE = 0.05

# ── Phase 5 §7 Compatibility Constants ───────────────────────────────────────
# Strategy families that are inappropriate during event windows (earnings/FED).
# These families assume stable vol — an event causes IV crush / expansion that
# breaks the thesis before the strategy can expire.
_EVENT_EXCLUDED_FAMILIES = frozenset({
    "calendar",          # front-month decays too fast vs back-month into event
    "double_calendar",
    "diagonal",          # similar vol-timing mismatch
    "condor",            # broad range assumption shattered by binary event
    "butterfly",         # pin-risk strategy — needs stable price
    "ratio_spread",      # undefined-risk on large moves
    "backspread",
})

# Strategy families explicitly designed for event windows
_EVENT_DESIGNED_FAMILIES = frozenset({
    "event_straddle",
    "event_strangle",
    "event_calendar",
    "earnings_condor",
    "earnings_butterfly",
    "pre_event_calendar",
    "post_event_crush",
    "0dte_event",
})

# Vol-thesis categories that are considered "neutral" (pass any vol filter)
_VOL_NEUTRAL = frozenset({"NEUTRAL", "ANY"})


def filter_compatible(
    catalog: "List[StrategySpec]",
    direction: str,
    iv_is_high: bool,
    dte_target: int            = 30,
    event_context: "Optional[str]" = None,
    expected_move_pct: "Optional[float]" = None,
    require_autonomous: bool   = True,
    require_defined_risk: bool = True,
) -> "Tuple[List[StrategySpec], List[Tuple[str, str]]]":
    """
    Phase 5 §7 — Pre-filter the strategy catalog to compatible candidates.

    Hard filters applied in order:
      1. AUTONOMOUS execution mode (when require_autonomous=True)
      2. Defined/Limited risk (when require_defined_risk=True)
      3. Direction gate: exclude directional mismatch
         - BULLISH thesis → exclude BEARISH strategies
         - BEARISH thesis → exclude BULLISH strategies
         - NEUTRAL thesis → all pass (vol gate below handles it)
      4. Volatility regime gate: exclude vol-thesis mismatch
         - HIGH_IV context → exclude LOW_IV-only strategies
         - LOW_IV context  → exclude HIGH_IV-only strategies
         - NEUTRAL/ANY vol_thesis always passes
      5. DTE range gate: strategy.min_dte <= dte_target <= strategy.max_dte
      6. Event context gate:
         - If event_context in ("EARNINGS","FED","BINARY"): exclude families in
           _EVENT_EXCLUDED_FAMILIES unless they are in _EVENT_DESIGNED_FAMILIES
         - Always allow _EVENT_DESIGNED_FAMILIES when an event is present

    Returns:
        (compatible_specs, [(rejected_name, reason), ...])
    """
    compatible: "List[StrategySpec]" = []
    rejected:   "List[Tuple[str, str]]" = []
    vol_regime  = "HIGH_IV" if iv_is_high else "LOW_IV"
    has_event   = event_context in ("EARNINGS", "FED", "BINARY", "FOMC")

    for spec in catalog:
        name = spec.name

        # Gate 1 — execution mode
        if require_autonomous and spec.execution_mode != MODE_AUTONOMOUS:
            rejected.append((name, f"ANALYSIS_ONLY: execution_mode={spec.execution_mode}"))
            continue

        # Gate 2 — risk class
        if require_defined_risk and spec.risk_class == RISK_UNDEFINED:
            rejected.append((name, f"UNDEFINED_RISK: risk_class={spec.risk_class}"))
            continue

        # Gate 3 — direction
        strat_dir = getattr(spec, "direction", "ANY")
        if direction == "BULLISH" and strat_dir == "BEARISH":
            rejected.append((name, f"DIR_MISMATCH: thesis=BULLISH strategy={strat_dir}"))
            continue
        if direction == "BEARISH" and strat_dir == "BULLISH":
            rejected.append((name, f"DIR_MISMATCH: thesis=BEARISH strategy={strat_dir}"))
            continue
        # NEUTRAL thesis: all directions pass (vol gate governs below)

        # Gate 4 — volatility regime
        strat_vol = getattr(spec, "vol_thesis", "ANY")
        if strat_vol not in _VOL_NEUTRAL:
            if vol_regime == "HIGH_IV" and strat_vol == "LOW_IV":
                rejected.append((name,
                    f"VOL_MISMATCH: context=HIGH_IV strategy_vol_thesis=LOW_IV"))
                continue
            if vol_regime == "LOW_IV" and strat_vol == "HIGH_IV":
                rejected.append((name,
                    f"VOL_MISMATCH: context=LOW_IV strategy_vol_thesis=HIGH_IV"))
                continue

        # Gate 5 — DTE range
        min_dte = getattr(spec, "min_dte", 0) or 0
        max_dte = getattr(spec, "max_dte", 365) or 365
        if dte_target < min_dte:
            rejected.append((name,
                f"DTE_TOO_SHORT: dte_target={dte_target} min_dte={min_dte}"))
            continue
        if dte_target > max_dte:
            rejected.append((name,
                f"DTE_TOO_LONG: dte_target={dte_target} max_dte={max_dte}"))
            continue

        # Gate 6 — event context
        family = getattr(spec, "family", "").lower()
        if has_event:
            if family in _EVENT_EXCLUDED_FAMILIES and family not in _EVENT_DESIGNED_FAMILIES:
                rejected.append((name,
                    f"EVENT_EXCLUDED: family={family} event={event_context}"))
                continue
        # When no event, exclude event-only strategies (they need event dynamics)
        if not has_event and family in _EVENT_DESIGNED_FAMILIES:
            rejected.append((name,
                f"EVENT_ONLY_NO_EVENT: family={family} event_context=None"))
            continue

        compatible.append(spec)

    return compatible, rejected


class CompatibilityResult:
    """Record of a filter_compatible() call for audit/logging."""
    def __init__(
        self,
        total_catalog: int,
        compatible: "List[StrategySpec]",
        rejected: "List[Tuple[str, str]]",
        direction: str,
        iv_is_high: bool,
        dte_target: int,
        event_context: "Optional[str]",
    ):
        self.total_catalog  = total_catalog
        self.compatible     = compatible
        self.rejected       = rejected
        self.direction      = direction
        self.iv_is_high     = iv_is_high
        self.dte_target     = dte_target
        self.event_context  = event_context

    @property
    def n_compatible(self) -> int:
        return len(self.compatible)

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)

    def summary(self) -> str:
        vol = "HIGH_IV" if self.iv_is_high else "LOW_IV"
        return (
            f"filter_compatible: {self.n_compatible}/{self.total_catalog} compatible "
            f"direction={self.direction} vol={vol} dte={self.dte_target} "
            f"event={self.event_context} rejected={self.n_rejected}"
        )

    def to_dict(self) -> dict:
        return {
            "total_catalog":  self.total_catalog,
            "n_compatible":   self.n_compatible,
            "n_rejected":     self.n_rejected,
            "direction":      self.direction,
            "iv_is_high":     self.iv_is_high,
            "dte_target":     self.dte_target,
            "event_context":  self.event_context,
            "compatible_names": [s.name for s in self.compatible],
            "rejection_summary": self.rejected[:20],  # cap log size
        }


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
        score_inputs_json: Optional[Dict[str, Any]] = None,  # Phase 5 §8 audit
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
        self.score_inputs_json  = score_inputs_json  # Phase 5 §8: all 12 real inputs

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
        "score_signal_quality":      sc.get("score_signal_quality"),
        "direction_confidence_used": sc.get("direction_confidence_used"),
        "score_inputs_json":         eval_.score_inputs_json,   # Phase 5 §8 audit
        "legs_json":           [lg.to_dict() for lg in eval_.legs],
        "payoff_grid":         p.get("payoff_grid"),
        "iv_rank":             eval_.iv_rank,
    }
