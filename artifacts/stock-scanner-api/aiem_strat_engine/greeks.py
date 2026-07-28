"""
greeks.py — Greek aggregation, validation, and charm/vanna/vomma handling.
Uses Black-Scholes to fill missing higher-order greeks when Tradier omits them.
"""
from __future__ import annotations
import math
from typing import List, Optional, Dict
from .legs import Leg, SIDE_LONG, SIDE_SHORT
from .payoff import _N, bs_call


# ── Black-Scholes greek derivations ─────────────────────────────────────────

def _phi(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5*x*x) / math.sqrt(2*math.pi)

def _bs_params(S: float, K: float, T: float, sigma: float, r: float = 0.0):
    """Compute d1, d2 for Black-Scholes."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None, None
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma*math.sqrt(T)
    return d1, d2

def bs_delta(S: float, K: float, T: float, sigma: float, call: bool = True, r: float = 0.0) -> float:
    d1, d2 = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    return _N(d1) if call else _N(d1) - 1.0

def bs_gamma(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    d1, _ = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    return _phi(d1) / (S * sigma * math.sqrt(T))

def bs_vega(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Vega per 1 (not per 1%); multiply by 0.01 for per-percent."""
    d1, _ = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    return S * _phi(d1) * math.sqrt(T)

def bs_theta(S: float, K: float, T: float, sigma: float, call: bool = True, r: float = 0.0) -> float:
    """Theta per day (negative for long options)."""
    d1, d2 = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    term1 = -(S * _phi(d1) * sigma) / (2 * math.sqrt(T))
    if call:
        term2 = -r * K * math.exp(-r*T) * _N(d2)
    else:
        term2 = r * K * math.exp(-r*T) * _N(-d2)
    return (term1 + term2) / 365.0   # per calendar day

def bs_charm(S: float, K: float, T: float, sigma: float, call: bool = True, r: float = 0.0) -> float:
    """Charm = dDelta/dTime (delta decay per day)."""
    d1, d2 = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    charm = _phi(d1) * (r / (sigma*math.sqrt(T)) - d2 / (2*T))
    return charm / 365.0   # per day

def bs_vanna(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Vanna = dDelta/dVol = dVega/dSpot."""
    d1, d2 = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    return -_phi(d1) * d2 / sigma

def bs_vomma(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Vomma/Volga = d²V/dσ² (vega convexity)."""
    d1, d2 = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    return bs_vega(S, K, T, sigma, r) * d1 * d2 / sigma

def bs_rho(S: float, K: float, T: float, sigma: float, call: bool = True, r: float = 0.0) -> float:
    """
    Rho = dV/dr, expressed per 1 percentage-point change in r
    (standard convention: multiply by 0.01 to get sensitivity per 1 bp).
    Call rho  =  K·T·e^{-rT}·N(d2)  / 100
    Put  rho  = -K·T·e^{-rT}·N(-d2) / 100
    Reference: Hull, *Options, Futures, and Other Derivatives* (10th ed.) §19.6
               (Table 19.4: ATM call S=K=49, T=20/52, σ=0.20, r=0.05 → ρ≈8.91)
    """
    d1, d2 = _bs_params(S, K, T, sigma, r)
    if d1 is None: return 0.0
    disc = math.exp(-r * T)
    if call:
        return K * T * disc * _N(d2) / 100.0
    else:
        return -K * T * disc * _N(-d2) / 100.0


# ── Aggregate greeks across legs ─────────────────────────────────────────────

def aggregate(legs: List[Leg]) -> Dict[str, Optional[float]]:
    """
    Sum signed greeks across all legs.
    For any greek that is None in any leg, fills via Black-Scholes approximation
    using the leg's available data (S=underlying proxied from delta, K=strike, etc.).
    Returns per-unit-of-one-contract values.
    """
    keys = ["delta", "gamma", "theta", "vega", "rho", "charm", "vanna", "vomma"]
    totals = {k: 0.0 for k in keys}

    for lg in legs:
        if lg.asset_type == "STOCK":
            mult = 1 if lg.side == SIDE_LONG else -1
            totals["delta"] += mult * lg.ratio * 1.0
            continue

        mult = lg.ratio * (1 if lg.side == SIDE_LONG else -1)
        call = (lg.asset_type == "CALL")

        # Use Tradier greeks where available; fall back to BS
        if lg.delta is not None:
            totals["delta"] += mult * lg.delta
        elif lg.strike and lg.iv and lg.dte:
            T = lg.dte / 365.0
            # Approximate spot from delta (crude: use ATM spot = strike)
            S = lg.strike
            totals["delta"] += mult * bs_delta(S, lg.strike, T, lg.iv, call)

        if lg.gamma is not None:
            totals["gamma"] += mult * lg.gamma
        elif lg.strike and lg.iv and lg.dte:
            T = lg.dte / 365.0
            totals["gamma"] += mult * bs_gamma(lg.strike, lg.strike, T, lg.iv)

        if lg.theta is not None:
            totals["theta"] += mult * lg.theta
        elif lg.strike and lg.iv and lg.dte:
            T = lg.dte / 365.0
            totals["theta"] += mult * bs_theta(lg.strike, lg.strike, T, lg.iv, call)

        if lg.vega is not None:
            totals["vega"] += mult * lg.vega
        elif lg.strike and lg.iv and lg.dte:
            T = lg.dte / 365.0
            totals["vega"] += mult * bs_vega(lg.strike, lg.strike, T, lg.iv)

        if lg.rho is not None:
            totals["rho"] += mult * lg.rho

        # Higher-order: always compute via BS since Tradier rarely provides these
        if lg.strike and lg.iv and lg.dte:
            T = lg.dte / 365.0
            totals["charm"] += mult * (lg.charm or bs_charm(lg.strike, lg.strike, T, lg.iv, call))
            totals["vanna"] += mult * (lg.vanna or bs_vanna(lg.strike, lg.strike, T, lg.iv))
            totals["vomma"] += mult * (lg.vomma or bs_vomma(lg.strike, lg.strike, T, lg.iv))
        elif lg.charm:
            totals["charm"] += mult * lg.charm
        if lg.vanna:
            totals["vanna"] += mult * lg.vanna
        if lg.vomma:
            totals["vomma"] += mult * lg.vomma

    return {k: round(v, 6) for k, v in totals.items()}


def portfolio_greeks(positions: List[Dict]) -> Dict[str, float]:
    """
    Aggregate portfolio-level greeks from multiple open positions.
    positions: list of dicts with greek values and quantities.
    """
    agg = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for pos in positions:
        qty = pos.get("quantity", 1)
        for g in agg:
            val = pos.get(g)
            if val is not None:
                agg[g] += val * qty
    return {k: round(v, 4) for k, v in agg.items()}
