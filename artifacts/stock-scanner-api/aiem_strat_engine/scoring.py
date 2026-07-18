"""
scoring.py — Capital Compounding Score computation.

The score rewards:
  + High probability of profit
  + Positive expected value after costs
  + Capital preservation (limited drawdown)
  + Defined-risk structure
  + Capital efficiency (return per dollar at risk)
  + Liquidity / fill quality
  + Thesis fit
  + Market regime fit
  + Volatility regime fit
  + Diversification value
  + Pattern confirmation (candlestick/chart/harmonic/Wyckoff/EW vs thesis)

And penalizes:
  - Max loss size
  - Drawdown / tail risk
  - Assignment risk
  - Event risk
  - Slippage cost
  - Structural complexity
  - Concentration risk

All component scores are stored separately in the DB for full transparency.
"""
from __future__ import annotations
import math
from typing import Optional, Dict, Any
from .config import SCORE_WEIGHTS, SCORE_PENALTIES, NO_TRADE_SCORE
from .legs import RISK_DEFINED, RISK_LIMITED, RISK_UNDEFINED


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def score_pop(pop: Optional[float]) -> float:
    """PoP component: 0→0, 0.5→0.5, 0.7→1.0 (linear, capped)."""
    if pop is None: return 0.0
    return _clamp((pop - 0.25) / 0.50)   # 25%=0, 75%=1


def score_ev(ev_after_costs: Optional[float]) -> float:
    """EV/risk component: negative EV → 0, EV=0.05 per $ at risk → 0.5, 0.10 → 1.0."""
    if ev_after_costs is None: return 0.0
    return _clamp((ev_after_costs + 0.05) / 0.10)


def score_capital_preservation(
    max_loss: Optional[float],
    max_profit: Optional[float],
    risk_class: str,
) -> float:
    """
    Reward defined-risk structures with good reward-to-risk.
    UNDEFINED risk → 0. No max_loss → 0.
    """
    if risk_class == RISK_UNDEFINED or max_loss is None:
        return 0.0
    if max_profit is None or max_loss == 0:
        return 0.3  # undefined profit but defined loss — limited credit
    rr = max_profit / max_loss
    return _clamp(0.2 + rr * 0.30)   # rr=2.0 → 0.8, rr=3.0 → 1.0+


def score_defined_risk(risk_class: str, execution_mode: str) -> float:
    """
    DEFINED_RISK = 1.0, LIMITED_RISK = 0.6, UNDEFINED_RISK = 0.0.
    ANALYSIS_ONLY capped at 0.3 (won't be paper-traded anyway).
    """
    if execution_mode != "AUTONOMOUS":
        return 0.3
    mapping = {RISK_DEFINED: 1.0, RISK_LIMITED: 0.60, RISK_UNDEFINED: 0.0}
    return mapping.get(risk_class, 0.0)


def score_capital_efficiency(
    ev_after_costs: Optional[float],
    return_on_risk: Optional[float],
) -> float:
    """EV per dollar at risk × return on risk (% of max loss per unit time)."""
    if ev_after_costs is None and return_on_risk is None:
        return 0.0
    ev_part  = _clamp((ev_after_costs or 0.0) * 5.0)
    ror_part = _clamp((return_on_risk or 0.0) / 0.50)
    return (ev_part + ror_part) / 2.0


def score_liquidity(liquidity: float) -> float:
    """Pass-through of liquidity score [0,1]."""
    return _clamp(liquidity)


def score_thesis_fit(
    strategy_direction: str,
    thesis: str,
    strategy_vol_thesis: str,
    vol_regime: str,
) -> float:
    """
    Reward strategies that align with the stated thesis and vol regime.
    """
    dir_match  = 1.0 if strategy_direction in (thesis, "ANY", "NEUTRAL") else 0.2
    vol_match  = 1.0 if strategy_vol_thesis in (vol_regime, "NEUTRAL", "ANY") else 0.4
    return (dir_match * 0.6 + vol_match * 0.4)


def score_regime_fit(
    strategy_direction: str,
    market_regime: str,
) -> float:
    """Reward strategies that suit the current market regime."""
    regime_bull = {"BULL_TREND", "RECOVERY", "BREAKOUT"}
    regime_bear = {"BEAR_TREND", "BREAKDOWN", "CONTRACTION"}
    regime_neut = {"SIDEWAYS", "RANGING", "LOW_VOL", "HIGH_VOL"}

    if strategy_direction == "BULLISH" and market_regime in regime_bull:
        return 1.0
    if strategy_direction == "BEARISH" and market_regime in regime_bear:
        return 1.0
    if strategy_direction == "NEUTRAL" and market_regime in regime_neut:
        return 1.0
    if strategy_direction in ("ANY", "NEUTRAL"):
        return 0.6
    return 0.3


def score_vol_fit(
    strategy_vol_thesis: str,
    iv_rank: Optional[float],
) -> float:
    """Match vol structure to IV rank."""
    if iv_rank is None:
        return 0.5  # neutral if unknown
    is_high_iv = iv_rank >= 50
    if strategy_vol_thesis == "HIGH_IV" and is_high_iv:     return 1.0
    if strategy_vol_thesis == "LOW_IV"  and not is_high_iv: return 1.0
    if strategy_vol_thesis in ("NEUTRAL", "ANY"):           return 0.7
    return 0.2


def score_diversification(
    strategy_family: str,
    existing_families: Optional[list] = None,
) -> float:
    """Reward strategies that diversify from existing positions."""
    if not existing_families:
        return 0.5   # no context — neutral
    if strategy_family not in existing_families:
        return 1.0
    # Count concentration
    count = existing_families.count(strategy_family)
    return _clamp(1.0 - count * 0.2)


def penalty_max_loss(max_loss: Optional[float], capital: float) -> float:
    """Penalty for large max_loss relative to portfolio."""
    if max_loss is None:
        return SCORE_PENALTIES["max_loss_pct"] * 3.0   # heavy penalty for undefined
    bp = max_loss * 100  # per-contract dollars
    frac = bp / max(capital, 1.0)
    return SCORE_PENALTIES["max_loss_pct"] * frac * 10


def penalty_tail_risk(
    pop_fat_tail: Optional[float],
    pop_lognormal: Optional[float],
) -> float:
    """Penalty when fat-tail PoP is significantly below lognormal PoP."""
    if pop_fat_tail is None or pop_lognormal is None:
        return 0.0
    gap = max(0.0, pop_lognormal - pop_fat_tail)
    return SCORE_PENALTIES["tail_risk"] * gap * 5.0


def penalty_assignment_risk(assignment_risk: str) -> float:
    return SCORE_PENALTIES["assignment_risk"] if assignment_risk == "HIGH" else 0.0


def penalty_slippage(slippage: float, capital_at_risk: float) -> float:
    if capital_at_risk <= 0: return 0.0
    slip_frac = slippage / max(capital_at_risk, 0.01)
    return SCORE_PENALTIES["slippage_cost"] * slip_frac * 10


def penalty_complexity(n_legs: int) -> float:
    extra = max(0, n_legs - 2)
    return SCORE_PENALTIES["complexity"] * extra


def compute_capital_compounding_score(
    pop:                Optional[float],
    ev_after_costs:     Optional[float],
    max_loss:           Optional[float],
    max_profit:         Optional[float],
    risk_class:         str,
    execution_mode:     str,
    liquidity:          float,
    strategy_direction: str,
    strategy_vol_thesis:str,
    strategy_family:    str,
    thesis:             str,
    market_regime:      str,
    vol_regime:         str,
    iv_rank:            Optional[float],
    return_on_risk:     Optional[float],
    assignment_risk:    str,
    pop_fat_tail:       Optional[float] = None,
    pop_lognormal:      Optional[float] = None,
    slippage:           float           = 0.0,
    capital_at_risk:    float           = 1000.0,
    n_legs:             int             = 2,
    existing_families:  Optional[list]  = None,
    portfolio_capital:  float           = 100_000.0,
    pattern_score:      float           = 0.5,
) -> Dict[str, float]:
    """
    Compute the Capital Compounding Score and all individual components.
    Returns a dict with every component and the final score.
    """
    w = SCORE_WEIGHTS

    # Positive components
    sc_pop     = score_pop(pop)
    sc_ev      = score_ev(ev_after_costs)
    sc_capres  = score_capital_preservation(max_loss, max_profit, risk_class)
    sc_def     = score_defined_risk(risk_class, execution_mode)
    sc_capeff  = score_capital_efficiency(ev_after_costs, return_on_risk)
    sc_liq     = score_liquidity(liquidity)
    sc_thesis  = score_thesis_fit(strategy_direction, thesis, strategy_vol_thesis, vol_regime)
    sc_regime  = score_regime_fit(strategy_direction, market_regime)
    sc_vol     = score_vol_fit(strategy_vol_thesis, iv_rank)
    sc_divers  = score_diversification(strategy_family, existing_families)
    sc_pattern = _clamp(float(pattern_score))

    raw_score = (
        sc_pop     * w["pop"]                   +
        sc_ev      * w["ev_after_costs"]         +
        sc_capres  * w["capital_preservation"]   +
        sc_def     * w["defined_risk_quality"]   +
        sc_capeff  * w["capital_efficiency"]     +
        sc_liq     * w["liquidity"]              +
        sc_thesis  * w["thesis_fit"]             +
        sc_regime  * w["regime_fit"]             +
        sc_vol     * w["vol_regime_fit"]         +
        sc_divers  * w["diversification_value"]  +
        sc_pattern * w["pattern_confirmation"]
    )

    # Penalties
    pen_loss   = penalty_max_loss(max_loss, portfolio_capital)
    pen_tail   = penalty_tail_risk(pop_fat_tail, pop_lognormal)
    pen_assign = penalty_assignment_risk(assignment_risk)
    pen_slip   = penalty_slippage(slippage, capital_at_risk)
    pen_comp   = penalty_complexity(n_legs)
    total_penalty = pen_loss + pen_tail + pen_assign + pen_slip + pen_comp

    final_score = _clamp(raw_score - total_penalty)

    return {
        "score_pop":                  round(sc_pop,     4),
        "score_ev":                   round(sc_ev,      4),
        "score_capital_pres":         round(sc_capres,  4),
        "score_defined_risk":         round(sc_def,     4),
        "score_cap_efficiency":       round(sc_capeff,  4),
        "score_liquidity":            round(sc_liq,     4),
        "score_thesis_fit":           round(sc_thesis,  4),
        "score_regime_fit":           round(sc_regime,  4),
        "score_vol_fit":              round(sc_vol,     4),
        "score_diversification":      round(sc_divers,  4),
        "score_pattern_confirmation": round(sc_pattern, 4),
        "penalty_total":              round(total_penalty, 4),
        "capital_compounding_score":  round(final_score,   4),
    }


def no_trade_score(
    thesis: str,
    market_regime: str,
    iv_rank: Optional[float],
) -> float:
    """
    Score for the NO_TRADE decision.
    Higher in uncertain/low-conviction conditions.
    Base = NO_TRADE_SCORE; adjust for regime clarity.
    """
    base = NO_TRADE_SCORE
    # In trending markets with clear thesis, NO_TRADE is less attractive
    clear_regimes = {"BULL_TREND", "BEAR_TREND", "BREAKOUT", "BREAKDOWN"}
    if market_regime in clear_regimes:
        base -= 0.05
    # High IV rank: more opportunity available, lower NO_TRADE score
    if iv_rank and iv_rank > 60:
        base -= 0.03
    elif iv_rank and iv_rank < 20:
        base += 0.05  # low IV = poor premium, favor NO_TRADE
    return _clamp(base)
