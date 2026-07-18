"""
aiem_portfolio_engine/valuation.py — S6: Liquidity-Adjusted Portfolio Valuation.

Does not value the portfolio at midpoint prices only.
Computes mid, conservative executable, estimated liquidation cost,
multi-leg exit cost, and liquidity-adjusted P/L and max-loss.

NOT_IMPLEMENTED v1: market depth (no L2 feed — same constraint as EI v1).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .snapshot import PortfolioSnapshot, PortfolioPosition
from .config import CONTRACT_MULTIPLIER, LIQUIDITY_ADJ_LOSS_LIMIT, NOT_IMPLEMENTED_V1


def _spread_cost_fraction(
    bid: Optional[float],
    ask: Optional[float],
    mid: Optional[float],
) -> float:
    """Half-spread as fraction of mid price (mirrors EI v1 convention)."""
    if mid and mid > 0 and bid is not None and ask is not None:
        return (ask - bid) / (2 * mid)
    return 0.15   # conservative fallback if no quote


def _volume_liquidity_adj(volume: int) -> float:
    """Volume-based liquidity discount on mid value: 0.0 = full mid, 1.0 = worthless."""
    if volume >= 500:   return 0.00
    if volume >= 100:   return 0.02
    if volume >= 20:    return 0.05
    if volume >= 5:     return 0.10
    return 0.20


def _oi_liquidity_adj(oi: int) -> float:
    """Open interest liquidity discount."""
    if oi >= 2000:  return 0.00
    if oi >= 500:   return 0.01
    if oi >= 50:    return 0.03
    return 0.08


@dataclass
class LegValuation:
    leg_number:           int
    asset_type:           str
    mid_value:            float
    conservative_value:   float
    exit_cost_per_unit:   float   # $/contract spread cost to close
    liquidity_discount:   float   # fraction [0,1]


@dataclass
class PositionValuation:
    paper_trade_id:        str
    ticker:                str
    mid_value:             float
    conservative_value:    float
    liquidation_cost:      float
    multi_leg_exit_cost:   float
    partial_fill_risk:     str     # LOW / MEDIUM / HIGH / NOT_IMPLEMENTED
    legs:                  List[LegValuation] = field(default_factory=list)


@dataclass
class LiquidityValuation:
    mid_portfolio_value:        float
    conservative_portfolio_value: float
    estimated_liquidation_cost:  float
    multi_leg_exit_cost:         float
    partial_fill_risk_score:     float    # 0 = low, 1 = high
    liquidity_adjusted_pl:       float
    liquidity_adjusted_max_loss: float
    liquidity_limit_breach:      bool
    breach_details:              str
    not_implemented_items:       List[str] = field(default_factory=list)
    positions:                   List[PositionValuation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mid_portfolio_value": round(self.mid_portfolio_value, 2),
            "conservative_portfolio_value": round(self.conservative_portfolio_value, 2),
            "estimated_liquidation_cost": round(self.estimated_liquidation_cost, 2),
            "multi_leg_exit_cost": round(self.multi_leg_exit_cost, 2),
            "partial_fill_risk_score": round(self.partial_fill_risk_score, 4),
            "liquidity_adjusted_pl": round(self.liquidity_adjusted_pl, 2),
            "liquidity_adjusted_max_loss": round(self.liquidity_adjusted_max_loss, 2),
            "liquidity_limit_breach": self.liquidity_limit_breach,
            "breach_details": self.breach_details,
            "not_implemented_items": self.not_implemented_items,
        }


def _value_position(pos: PortfolioPosition) -> PositionValuation:
    """Compute liquidity-adjusted valuation for one open position."""
    leg_vals: List[LegValuation] = []
    mid_total, cons_total, exit_cost_total = 0.0, 0.0, 0.0
    n_option_legs = 0
    max_partial_risk = 0.0

    for lg in pos.legs:
        qty = lg.quantity
        if lg.asset_type == "STOCK":
            spot_mid = (lg.bid or 0) + (lg.ask or 0)
            lv = LegValuation(
                leg_number         = lg.leg_number,
                asset_type         = "STOCK",
                mid_value          = spot_mid * qty,
                conservative_value = spot_mid * qty * 0.995,
                exit_cost_per_unit = 0.0,
                liquidity_discount = 0.0,
            )
            leg_vals.append(lv)
            mid_total  += lv.mid_value
            cons_total += lv.conservative_value
            continue

        n_option_legs += 1
        mid   = lg.mid or 0.0
        bid   = lg.bid
        ask   = lg.ask
        vol   = 0
        oi    = 0

        spread_frac = _spread_cost_fraction(bid, ask, mid)
        vol_disc    = _volume_liquidity_adj(vol)
        oi_disc     = _oi_liquidity_adj(oi)
        total_disc  = min(spread_frac + vol_disc + oi_disc, 0.50)

        mid_val   = mid * qty * CONTRACT_MULTIPLIER
        cons_val  = mid_val * (1.0 - total_disc)
        exit_cost = spread_frac * mid_val * 2   # round-trip spread on close

        max_partial_risk = max(max_partial_risk, spread_frac)

        lv = LegValuation(
            leg_number         = lg.leg_number,
            asset_type         = lg.asset_type,
            mid_value          = round(mid_val, 4),
            conservative_value = round(cons_val, 4),
            exit_cost_per_unit = round(exit_cost, 4),
            liquidity_discount = round(total_disc, 4),
        )
        leg_vals.append(lv)
        mid_total      += mid_val
        cons_total     += cons_val
        exit_cost_total += exit_cost

    multi_leg_penalty = exit_cost_total * (0.25 * max(0, n_option_legs - 1))
    liquidation_cost  = exit_cost_total + multi_leg_penalty

    if max_partial_risk < 0.05:
        partial_risk = "LOW"
    elif max_partial_risk < 0.15:
        partial_risk = "MEDIUM"
    else:
        partial_risk = "HIGH"

    return PositionValuation(
        paper_trade_id      = pos.paper_trade_id,
        ticker              = pos.ticker,
        mid_value           = round(mid_total, 2),
        conservative_value  = round(cons_total, 2),
        liquidation_cost    = round(liquidation_cost, 2),
        multi_leg_exit_cost = round(multi_leg_penalty, 2),
        partial_fill_risk   = partial_risk,
        legs                = leg_vals,
    )


def _candidate_liquidation_cost(candidate_legs: List[Dict]) -> float:
    """Estimate one-way liquidation cost for the candidate."""
    total = 0.0
    for cl in candidate_legs:
        mid  = float(cl.get("mid") or 0)
        bid  = cl.get("bid")
        ask  = cl.get("ask")
        qty  = int(cl.get("quantity", 1))
        spread_frac = _spread_cost_fraction(bid, ask, mid)
        total += spread_frac * mid * qty * CONTRACT_MULTIPLIER * 2
    return round(total, 2)


def compute_liquidity_adjusted_valuation(
    snapshot: PortfolioSnapshot,
    candidate_legs: Optional[List[Dict]] = None,
    candidate_capital: float = 0.0,
) -> LiquidityValuation:
    """
    Compute liquidity-adjusted portfolio valuation.
    candidate_legs: the proposed new position's legs (list of dicts).
    """
    candidate_legs = candidate_legs or []
    pos_vals = [_value_position(p) for p in snapshot.positions]

    mid_total  = sum(p.mid_value for p in pos_vals)
    cons_total = sum(p.conservative_value for p in pos_vals)
    liq_cost   = sum(p.liquidation_cost for p in pos_vals)
    ml_cost    = sum(p.multi_leg_exit_cost for p in pos_vals)

    partial_risk_vals = {
        "LOW": 0.1, "MEDIUM": 0.4, "HIGH": 0.8, "NOT_IMPLEMENTED": 0.5,
    }
    avg_partial = sum(
        partial_risk_vals.get(p.partial_fill_risk, 0.5) for p in pos_vals
    ) / max(len(pos_vals), 1)

    cand_liq_cost = _candidate_liquidation_cost(candidate_legs)

    liq_adj_pl      = cons_total - mid_total
    liq_adj_max_loss = (
        sum(p.maximum_loss for p in snapshot.positions)
        + candidate_capital
        + liq_cost
        + cand_liq_cost
    )

    breach = liq_adj_max_loss > LIQUIDITY_ADJ_LOSS_LIMIT
    breach_detail = (
        f"liq-adj max loss ${liq_adj_max_loss:.2f} > ${LIQUIDITY_ADJ_LOSS_LIMIT:.2f}"
        if breach else ""
    )

    return LiquidityValuation(
        mid_portfolio_value         = round(mid_total, 2),
        conservative_portfolio_value= round(cons_total, 2),
        estimated_liquidation_cost  = round(liq_cost + cand_liq_cost, 2),
        multi_leg_exit_cost         = round(ml_cost, 2),
        partial_fill_risk_score     = round(avg_partial, 4),
        liquidity_adjusted_pl       = round(liq_adj_pl, 2),
        liquidity_adjusted_max_loss = round(liq_adj_max_loss, 2),
        liquidity_limit_breach      = breach,
        breach_details              = breach_detail,
        not_implemented_items       = [NOT_IMPLEMENTED_V1[1]],  # market depth
        positions                   = pos_vals,
    )
