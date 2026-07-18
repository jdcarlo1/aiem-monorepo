"""
aiem_portfolio_engine/greeks.py — S2: Aggregate Portfolio Greeks.

Computes all 8 required Greeks (delta, gamma, vega, theta, rho, charm,
vanna, vomma) plus stock-equivalent delta across all open positions.

Formula per leg:
    POSITION_GREEK = LEG_GREEK × quantity × CONTRACT_MULTIPLIER × direction
    direction: LONG=+1, SHORT=-1
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from .snapshot import PortfolioSnapshot, PortfolioPosition, PositionLeg
from .config import CONTRACT_MULTIPLIER

try:
    from aiem_strat_engine.greeks import (
        bs_delta, bs_gamma, bs_vega, bs_theta,
        bs_charm, bs_vanna, bs_vomma,
    )
except ImportError:
    def _bs_params(S, K, T, sigma):
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return None, None
        import math
        d1 = (math.log(S/K) + 0.5*sigma**2*T) / (sigma*math.sqrt(T))
        return d1, d1 - sigma*math.sqrt(T)
    def _N(x):
        import math
        return 0.5*(1+math.erf(x/math.sqrt(2)))
    def _phi(x):
        import math
        return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)
    def bs_delta(S,K,T,sigma,call=True,r=0.0):
        d1,_ = _bs_params(S,K,T,sigma); return (_N(d1) if call else _N(d1)-1) if d1 else 0.0
    def bs_gamma(S,K,T,sigma,r=0.0):
        d1,_ = _bs_params(S,K,T,sigma); return _phi(d1)/(S*sigma*math.sqrt(T)) if d1 else 0.0
    def bs_vega(S,K,T,sigma,r=0.0):
        d1,_ = _bs_params(S,K,T,sigma); return S*_phi(d1)*math.sqrt(T) if d1 else 0.0
    def bs_theta(S,K,T,sigma,call=True,r=0.0):
        d1,d2 = _bs_params(S,K,T,sigma)
        if d1 is None: return 0.0
        return (-(S*_phi(d1)*sigma)/(2*math.sqrt(T)) + (-r*K*math.exp(-r*T)*_N(d2) if call else r*K*math.exp(-r*T)*_N(-d2)))/365.0
    def bs_charm(S,K,T,sigma,call=True,r=0.0):
        d1,d2 = _bs_params(S,K,T,sigma)
        if d1 is None: return 0.0
        return _phi(d1)*(r/(sigma*math.sqrt(T)) - d2/(2*T))/365.0
    def bs_vanna(S,K,T,sigma,r=0.0):
        d1,d2 = _bs_params(S,K,T,sigma)
        return -_phi(d1)*d2/sigma if d1 else 0.0
    def bs_vomma(S,K,T,sigma,r=0.0):
        d1,d2 = _bs_params(S,K,T,sigma)
        return bs_vega(S,K,T,sigma)*d1*d2/sigma if d1 else 0.0


@dataclass
class PortfolioGreeks:
    delta:            float = 0.0
    gamma:            float = 0.0
    theta:            float = 0.0
    vega:             float = 0.0
    rho:              float = 0.0
    charm:            float = 0.0
    vanna:            float = 0.0
    vomma:            float = 0.0
    stock_equiv_delta: float = 0.0
    total_delta:      float = 0.0
    n_positions:      int   = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta": round(self.delta, 6),
            "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 6),
            "vega":  round(self.vega, 6),
            "rho":   round(self.rho, 6),
            "charm": round(self.charm, 6),
            "vanna": round(self.vanna, 6),
            "vomma": round(self.vomma, 6),
            "stock_equiv_delta": round(self.stock_equiv_delta, 6),
            "total_delta": round(self.total_delta, 6),
            "n_positions": self.n_positions,
        }


def _leg_direction(buy_or_sell: str) -> int:
    return +1 if str(buy_or_sell).upper() in ("LONG", "BUY") else -1


def _compute_leg_greeks(
    leg: PositionLeg,
    spot: Optional[float],
) -> Dict[str, float]:
    """
    Returns signed per-position greek contribution for one leg.
    Multiplied by quantity × CONTRACT_MULTIPLIER × direction.
    """
    direction = _leg_direction(leg.buy_or_sell)
    qty       = leg.quantity
    mult      = qty * CONTRACT_MULTIPLIER * direction

    if leg.asset_type == "STOCK":
        return {
            "delta": mult * 1.0,
            "gamma": 0.0, "theta": 0.0, "vega": 0.0,
            "rho": 0.0, "charm": 0.0, "vanna": 0.0, "vomma": 0.0,
            "stock_contribution": mult * 1.0,
        }

    call = (leg.asset_type == "CALL")
    S    = spot or leg.strike or 100.0
    K    = leg.strike or S
    T    = max((leg.dte_at_entry or 14) / 365.0, 1/365.0)
    iv   = leg.iv or 0.30

    # Use stored greeks where available; fill with BS otherwise
    delta = (leg.delta if leg.delta is not None
             else bs_delta(S, K, T, iv, call))
    gamma = (leg.gamma if leg.gamma is not None
             else bs_gamma(S, K, T, iv))
    theta = (leg.theta if leg.theta is not None
             else bs_theta(S, K, T, iv, call))
    vega  = (leg.vega if leg.vega is not None
             else bs_vega(S, K, T, iv))
    rho   = (leg.rho if leg.rho is not None else 0.0)
    charm = bs_charm(S, K, T, iv, call)
    vanna = bs_vanna(S, K, T, iv)
    vomma = bs_vomma(S, K, T, iv)

    return {
        "delta": mult * delta,
        "gamma": mult * gamma,
        "theta": mult * theta,
        "vega":  mult * vega,
        "rho":   mult * rho,
        "charm": mult * charm,
        "vanna": mult * vanna,
        "vomma": mult * vomma,
        "stock_contribution": 0.0,
    }


def compute_portfolio_greeks(
    positions: List[PortfolioPosition],
    candidate_legs: Optional[List[Dict]] = None,
    candidate_spot: Optional[float] = None,
) -> PortfolioGreeks:
    """
    Aggregate portfolio Greeks across all open positions.
    If candidate_legs is provided, they are included in the computation
    (used for AFTER greek calculation).

    candidate_legs: list of dicts matching PositionLeg field names + buy_or_sell.
    """
    keys = ["delta", "gamma", "theta", "vega", "rho", "charm", "vanna", "vomma"]
    totals = {k: 0.0 for k in keys}
    stock_equiv = 0.0

    for pos in positions:
        spot = pos.underlying_price
        for lg in pos.legs:
            g = _compute_leg_greeks(lg, spot)
            for k in keys:
                totals[k] += g[k]
            stock_equiv += g.get("stock_contribution", 0.0)

    if candidate_legs:
        for cl in candidate_legs:
            pseudo = PositionLeg(
                leg_number   = cl.get("leg_number", 1),
                asset_type   = cl.get("asset_type", cl.get("contract_type", "CALL")).upper(),
                call_or_put  = cl.get("call_or_put"),
                buy_or_sell  = cl.get("buy_or_sell", cl.get("action", "LONG")),
                quantity     = int(cl.get("quantity", 1)),
                ratio        = float(cl.get("ratio", 1.0)),
                strike       = cl.get("strike"),
                expiration   = cl.get("expiration_date"),
                dte_at_entry = cl.get("dte", 14),
                bid          = cl.get("bid"),
                ask          = cl.get("ask"),
                mid          = cl.get("mid"),
                iv           = cl.get("implied_volatility", cl.get("iv")),
                delta        = cl.get("delta"),
                gamma        = cl.get("gamma"),
                theta        = cl.get("theta"),
                vega         = cl.get("vega"),
                rho          = cl.get("rho"),
            )
            g = _compute_leg_greeks(pseudo, candidate_spot or pseudo.strike)
            for k in keys:
                totals[k] += g[k]
            stock_equiv += g.get("stock_contribution", 0.0)

    total_delta = totals["delta"] + stock_equiv
    n = len(positions) + (1 if candidate_legs else 0)

    return PortfolioGreeks(
        delta             = round(totals["delta"], 6),
        gamma             = round(totals["gamma"], 6),
        theta             = round(totals["theta"], 6),
        vega              = round(totals["vega"], 6),
        rho               = round(totals["rho"], 6),
        charm             = round(totals["charm"], 6),
        vanna             = round(totals["vanna"], 6),
        vomma             = round(totals["vomma"], 6),
        stock_equiv_delta = round(stock_equiv, 6),
        total_delta       = round(total_delta, 6),
        n_positions       = n,
    )


def save_portfolio_greeks(
    snapshot_id: str,
    phase: str,
    greeks: PortfolioGreeks,
    db_url: str,
) -> int:
    """Insert a PortfolioGreeks row. Returns the auto-generated id."""
    import psycopg2
    with psycopg2.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ape_portfolio_greeks
                    (snapshot_id, phase, portfolio_delta, portfolio_gamma,
                     portfolio_theta, portfolio_vega, portfolio_rho,
                     portfolio_charm, portfolio_vanna, portfolio_vomma,
                     stock_equiv_delta, total_delta, n_positions)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                snapshot_id, phase,
                greeks.delta, greeks.gamma, greeks.theta, greeks.vega, greeks.rho,
                greeks.charm, greeks.vanna, greeks.vomma,
                greeks.stock_equiv_delta, greeks.total_delta, greeks.n_positions,
            ))
            row_id = cur.fetchone()[0]
        conn.commit()
    return row_id
