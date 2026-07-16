"""
payoff.py — Per-strategy payoff calculations.

For all strategies EXCEPT calendars/diagonals, payoff at expiry is computed
from intrinsic value across a price grid (no model needed).

For calendars and diagonals, the back-month leg has time value remaining at
front expiry — we use Black-Scholes to model that residual value, then
compute payoff at the front expiry date.

All functions return per-contract, per-unit values (multiply by 100 for dollars).
"""
from __future__ import annotations
import math
from typing import List, Optional, Tuple
from .legs import Leg, ASSET_CALL, ASSET_PUT, ASSET_STOCK, SIDE_LONG, SIDE_SHORT

# ── Standard normal CDF (Abramowitz & Stegun 26.2.17) ───────────────────────
def _N(x: float) -> float:
    if x < -10: return 0.0
    if x > 10: return 1.0
    a1,a2,a3,a4,a5 = 0.319381530,-0.356563782,1.781477937,-1.821255978,1.330274429
    k = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = k*(a1+k*(a2+k*(a3+k*(a4+k*a5))))
    base = 1.0 - (1.0/math.sqrt(2*math.pi))*math.exp(-0.5*x*x)*poly
    return base if x >= 0 else 1.0 - base

def bs_call(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """European call via Black-Scholes. T in years."""
    if T <= 0: return max(0.0, S - K)
    if sigma <= 0: return max(0.0, S - K)
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma*math.sqrt(T)
    return S*_N(d1) - K*math.exp(-r*T)*_N(d2)

def bs_put(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """European put via Black-Scholes. T in years."""
    if T <= 0: return max(0.0, K - S)
    if sigma <= 0: return max(0.0, K - S)
    return bs_call(S, K, T, sigma, r) - S + K*math.exp(-r*T)

def _leg_value_at_price(leg: Leg, price: float, use_bs: bool = False,
                         t_remaining: float = 0.0) -> float:
    """
    Value of one leg at a given underlying price.
    use_bs=True : value via Black-Scholes (for back-month calendar legs).
    use_bs=False: intrinsic value only (at expiry).
    """
    if leg.asset_type == ASSET_STOCK:
        raw = price
    elif leg.asset_type == ASSET_CALL:
        if use_bs and t_remaining > 0 and leg.iv:
            raw = bs_call(price, leg.strike, t_remaining, leg.iv)
        else:
            raw = max(0.0, price - leg.strike)
    else:  # PUT
        if use_bs and t_remaining > 0 and leg.iv:
            raw = bs_put(price, leg.strike, t_remaining, leg.iv)
        else:
            raw = max(0.0, leg.strike - price)
    sign = 1 if leg.side == SIDE_LONG else -1
    return raw * leg.ratio * sign


def _find_breakevens(prices: List[float], payoffs: List[float]) -> List[float]:
    """Linear interpolation to find zero crossings in the payoff grid."""
    beps = []
    for i in range(len(payoffs)-1):
        p0, p1 = payoffs[i], payoffs[i+1]
        if (p0 < 0 and p1 >= 0) or (p0 >= 0 and p1 < 0):
            # linear interpolation
            frac = abs(p0) / (abs(p0) + abs(p1))
            beps.append(round(prices[i] + frac*(prices[i+1]-prices[i]), 4))
    return beps


def _price_grid(spot: float, n: int = 300) -> List[float]:
    """Logarithmically spaced price grid covering 0.2x–3.0x spot."""
    lo, hi = spot * 0.20, spot * 3.0
    step = (hi - lo) / (n - 1)
    return [lo + i*step for i in range(n)]


def _is_calendar_family(strategy_name: str) -> bool:
    sn = strategy_name.lower()
    return any(k in sn for k in ("calendar", "diagonal", "leaps calendar", "leaps diagonal"))


def compute_payoff(
    legs: List[Leg],
    strategy_name: str,
    spot: float,
    *,
    front_dte: Optional[int] = None,
    back_dte: Optional[int] = None,
) -> dict:
    """
    Compute payoff profile for the strategy.

    Returns:
        {
          "max_profit":  float | None,
          "max_loss":    float | None,   # positive means a loss
          "breakevens":  [float, ...],
          "payoff_grid": {"prices": [...], "payoffs": [...]},
          "net_cost":    float,          # positive=debit, negative=credit
          "is_undefined_risk": bool,
        }
    """
    prices = _price_grid(spot)

    # Determine if any leg uses calendar/diagonal back-month BS model
    is_cal = _is_calendar_family(strategy_name)

    # Identify front and back legs for calendars
    if is_cal and len(legs) >= 2:
        # Sort by expiration; front = earlier expiry
        exp_sorted = sorted([lg for lg in legs if lg.expiration], key=lambda l: l.expiration)
        front_exp = exp_sorted[0].expiration if exp_sorted else None
        back_legs  = {id(lg) for lg in legs if lg.expiration and lg.expiration != front_exp}
        # Use front DTE to determine remaining time for back leg at front expiry
        # Approximate: back_dte - front_dte days remain for back leg
        f_dte = front_dte or 30
        b_dte = back_dte or 60
        t_rem = max(0, b_dte - f_dte) / 365.0
    else:
        back_legs = set()
        t_rem = 0.0

    # Compute net entry cost (debit positive, credit negative)
    net_cost = 0.0
    for lg in legs:
        if lg.mid is None:
            net_cost = None
            break
        sign = 1 if lg.side == SIDE_LONG else -1
        net_cost = (net_cost or 0.0) + sign * (lg.mid or 0.0) * lg.ratio
    if net_cost is None:
        net_cost = 0.0

    # Compute payoff at each grid price
    payoffs = []
    for price in prices:
        total = -net_cost  # payoff is gain = value_at_price - entry_cost
        for lg in legs:
            use_bs = is_cal and id(lg) in back_legs
            total += _leg_value_at_price(lg, price, use_bs=use_bs, t_remaining=t_rem)
        payoffs.append(round(total, 6))

    # Identify max profit / max loss
    max_pnl = max(payoffs)
    min_pnl = min(payoffs)

    # Undefined-risk detection: loss grows without bound at price extremes.
    # For naked short call: payoff keeps DECLINING (more negative) as price rises → right edge decreasing.
    # For naked short put:  payoff keeps DECLINING (more negative) as price falls → left edge decreasing.
    is_undefined_right = (payoffs[-1] < payoffs[-3] < payoffs[-5]) and (payoffs[-1] < 0)
    is_undefined_left  = (payoffs[0]  < payoffs[2]  < payoffs[4])  and (payoffs[0]  < 0)

    if is_undefined_right:
        # Short naked exposure on upside — unlimited loss
        max_profit_val = max_pnl
        max_loss_val   = None
        is_undefined   = True
    elif is_undefined_left:
        max_profit_val = max_pnl
        max_loss_val   = None
        is_undefined   = True
    else:
        max_profit_val = max_pnl if max_pnl > 0 else 0.0
        max_loss_val   = abs(min_pnl) if min_pnl < 0 else 0.0
        is_undefined   = False

    breakevens = _find_breakevens(prices, payoffs)

    return {
        "max_profit":       max_profit_val,
        "max_loss":         max_loss_val,
        "breakevens":       breakevens,
        "payoff_grid":      {"prices": [round(p,4) for p in prices[::10]],
                             "payoffs": [round(v,6) for v in payoffs[::10]]},
        "net_cost":         round(net_cost, 6) if net_cost is not None else 0.0,
        "is_undefined_risk": is_undefined,
    }


def compute_stress_losses(
    legs: List[Leg],
    strategy_name: str,
    spot: float,
    scenarios: Optional[dict] = None,
) -> dict:
    """
    Compute P/L under stress scenarios.
    scenarios: dict of {label: price_multiplier}
    """
    if scenarios is None:
        scenarios = {
            "gap_down_10pct":   0.90,
            "gap_down_20pct":   0.80,
            "gap_down_30pct":   0.70,
            "gap_up_10pct":     1.10,
            "gap_up_20pct":     1.20,
            "gap_up_30pct":     1.30,
            "unchanged":        1.00,
        }
    net_cost = sum(
        (1 if lg.side == SIDE_LONG else -1) * (lg.mid or 0.0) * lg.ratio
        for lg in legs
    )
    is_cal = _is_calendar_family(strategy_name)
    results = {}
    for label, mult in scenarios.items():
        price = spot * mult
        total = -net_cost
        for lg in legs:
            total += _leg_value_at_price(lg, price, use_bs=False)
        results[label] = round(total, 4)
    return results


def expected_value(
    payoffs: List[float],
    prices: List[float],
    spot: float,
    sigma_annual: float,
    dte: int,
    skew_adj: float = 0.0,
) -> float:
    """
    Expected value via numerical integration against a lognormal density.
    Uses trapezoidal integration.
    skew_adj: additive skew correction to sigma (negative = put skew adds EV for puts).
              Typically around ±0.02 to ±0.10.
    """
    if dte <= 0 or sigma_annual <= 0:
        return 0.0
    T = dte / 365.0
    sigma = max(0.05, sigma_annual + skew_adj)
    # Lognormal PDF: f(S) = 1/(S*sigma*sqrt(T)*sqrt(2pi)) * exp(-((ln(S/F)+0.5*sigma^2*T)^2)/(2*sigma^2*T))
    F = spot  # forward ≈ spot (r=0 simplification)
    total_w = 0.0
    total_ev = 0.0
    for i in range(len(prices)-1):
        p_mid = 0.5*(prices[i]+prices[i+1])
        if p_mid <= 0: continue
        z = (math.log(p_mid/F) + 0.5*sigma**2*T) / (sigma*math.sqrt(T))
        pdf = (1/(p_mid*sigma*math.sqrt(T)*math.sqrt(2*math.pi))) * math.exp(-0.5*z*z)
        w = pdf * (prices[i+1] - prices[i])
        pnl_mid = 0.5*(payoffs[i]+payoffs[i+1])
        total_w  += w
        total_ev += w * pnl_mid
    # Normalize to account for truncated grid
    if total_w > 0:
        return total_ev / total_w
    return 0.0
