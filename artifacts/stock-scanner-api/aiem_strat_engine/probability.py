"""
probability.py — Probability calculations for the Advanced Strategy Engine.

Uses calibrated lognormal distribution adjusted for volatility skew and
expected move. Also computes fat-tail stress PoP using t-distribution.

Does NOT use delta as PoP (delta ≈ PoP only for ATM options; it is
systematically wrong for spreads, calendars, and multi-leg structures).
"""
from __future__ import annotations
import math
from typing import List, Optional
from .payoff import _N, bs_call, bs_put, _price_grid, _find_breakevens


def _lognormal_cdf(S: float, spot: float, sigma: float, T: float) -> float:
    """
    P(underlying < S) under lognormal risk-neutral measure.
    F = spot (r=0 approximation).
    """
    if S <= 0: return 0.0
    if T <= 0: return 1.0 if S > spot else 0.0
    if sigma <= 0: return 1.0 if S > spot else 0.0
    z = (math.log(S/spot) + 0.5*sigma**2*T) / (sigma*math.sqrt(T))
    return _N(-z)  # P(X < S) where X is lognormal


def _t_dist_cdf(x: float, nu: float = 4.0) -> float:
    """
    Approximate CDF of student-t distribution at x with nu degrees of freedom.
    Uses regularized incomplete beta function approximation.
    """
    # Simple numerical approximation for nu > 2
    try:
        if x == 0: return 0.5
        t2 = x*x
        # for nu=4 (fat-tail proxy): CDF = 0.5 + 0.5 * sign(x) * I(nu/(nu+t2); nu/2, 0.5)
        # We use a series approximation
        # For nu=4: CDF(x) = 0.5*(1 + x*(1+x^2/6)*sign_factor / sqrt(1+x^2/4)^3)
        if nu == 4:
            factor = x*(1 + x*x/6) / (1 + x*x/4)**1.5
            return min(1.0, max(0.0, 0.5 + 0.5*factor))
        # Fallback to normal for other nu
        return _N(x)
    except Exception:
        return _N(x)


def probability_of_profit(
    payoffs: List[float],
    prices: List[float],
    spot: float,
    iv: float,
    dte: int,
    skew: float = 0.0,
) -> float:
    """
    PoP via numerical integration of the lognormal density over the
    profitable price region. Adjusted for IV skew.

    iv: ATM implied volatility (annualized)
    skew: put skew (positive = bearish skew, negative = call skew)
    Returns probability in [0, 1].
    """
    if dte <= 0 or iv <= 0 or len(prices) == 0:
        return 0.0
    T = dte / 365.0
    sigma = max(0.05, iv)

    # Integrate probability density over profitable regions
    profitable_prob = 0.0
    for i in range(len(prices)-1):
        if payoffs[i] > 0 or payoffs[i+1] > 0:
            p_lo, p_hi = prices[i], prices[i+1]
            # Approximate as uniform within interval
            p_mid = 0.5*(p_lo + p_hi)
            if p_mid <= 0: continue
            # Lognormal PDF at p_mid
            z = (math.log(p_mid/spot) + 0.5*sigma**2*T) / (sigma*math.sqrt(T))
            pdf = (1.0/(p_mid*sigma*math.sqrt(T)*math.sqrt(2*math.pi))) * math.exp(-0.5*z*z)
            # Weight by what fraction of the interval is profitable
            if payoffs[i] >= 0 and payoffs[i+1] >= 0:
                frac = 1.0
            elif payoffs[i] >= 0 and payoffs[i+1] < 0:
                frac = abs(payoffs[i]) / (abs(payoffs[i]) + abs(payoffs[i+1]))
            elif payoffs[i] < 0 and payoffs[i+1] >= 0:
                frac = abs(payoffs[i+1]) / (abs(payoffs[i]) + abs(payoffs[i+1]))
            else:
                frac = 0.0
            profitable_prob += pdf * (p_hi - p_lo) * frac

    # Skew correction: positive skew (put premium) reduces bullish PoP
    skew_correction = -skew * 0.15   # empirical dampening factor
    pop = min(0.99, max(0.01, profitable_prob + skew_correction))
    return round(pop, 4)


def probability_of_touch(
    breakevens: List[float],
    spot: float,
    iv: float,
    dte: int,
) -> float:
    """
    Approximate probability of the underlying touching the nearest breakeven
    before expiry (barrier approximation using reflection principle).
    P(touch) ≈ 2 * P(expire beyond breakeven) for log-normal.
    """
    if not breakevens or dte <= 0 or iv <= 0:
        return 0.0
    T = dte / 365.0
    sigma = max(0.05, iv)
    # Nearest breakeven
    nearest = min(breakevens, key=lambda b: abs(b - spot))
    # P(S_T > nearest) for call side, P(S_T < nearest) for put side
    if nearest > spot:
        p_expire = 1 - _lognormal_cdf(nearest, spot, sigma, T)
    else:
        p_expire = _lognormal_cdf(nearest, spot, sigma, T)
    p_touch = min(0.99, 2.0 * p_expire)
    return round(p_touch, 4)


def probability_of_max_profit(
    max_profit_price: Optional[float],
    spot: float,
    iv: float,
    dte: int,
    tolerance: float = 0.02,
) -> float:
    """
    Probability of landing within ±tolerance of the max-profit price at expiry.
    For spreads: max profit zone is the whole range — use PoP calculation instead.
    For butterflies: max profit is a narrow region around the center strike.
    Returns probability in [0, 1].
    """
    if max_profit_price is None or dte <= 0 or iv <= 0:
        return 0.0
    T = dte / 365.0
    sigma = max(0.05, iv)
    lo = max_profit_price * (1 - tolerance)
    hi = max_profit_price * (1 + tolerance)
    p_lo = _lognormal_cdf(lo, spot, sigma, T)
    p_hi = _lognormal_cdf(hi, spot, sigma, T)
    return round(abs(p_hi - p_lo), 4)


def fat_tail_pop(
    payoffs: List[float],
    prices: List[float],
    spot: float,
    iv: float,
    dte: int,
    nu: float = 4.0,
) -> float:
    """
    PoP under a fat-tailed (student-t) return distribution.
    nu=4 is a common equity fat-tail parameter.
    """
    if dte <= 0 or iv <= 0:
        return 0.0
    T = dte / 365.0
    sigma_T = iv * math.sqrt(T) * math.sqrt(max(1, nu-2)/nu)  # t-distribution scaling
    profitable_prob = 0.0
    for i in range(len(prices)-1):
        if payoffs[i] > 0 or payoffs[i+1] > 0:
            p_mid = 0.5*(prices[i]+prices[i+1])
            if p_mid <= 0: continue
            # Log return to p_mid
            lr = math.log(p_mid / spot) / sigma_T
            # t-density
            coeff = math.gamma((nu+1)/2) / (math.sqrt(nu*math.pi)*math.gamma(nu/2))
            density = coeff * (1 + lr**2/nu)**(-(nu+1)/2) / (p_mid * sigma_T)
            frac = 1.0
            if payoffs[i] < 0 and payoffs[i+1] >= 0:
                frac = abs(payoffs[i+1]) / (abs(payoffs[i])+abs(payoffs[i+1]))
            elif payoffs[i] >= 0 and payoffs[i+1] < 0:
                frac = abs(payoffs[i]) / (abs(payoffs[i])+abs(payoffs[i+1]))
            elif payoffs[i] < 0:
                frac = 0.0
            profitable_prob += density * (prices[i+1]-prices[i]) * frac
    return round(min(0.99, max(0.01, profitable_prob)), 4)


def expected_value_after_costs(
    ev_before: float,
    commission: float,
    slippage: float,
    capital_at_risk: float,
) -> float:
    """
    EV after commissions and slippage, normalized per dollar at risk.
    Returns EV/dollar_at_risk — useful for cross-strategy comparison.
    """
    if capital_at_risk <= 0:
        return 0.0
    ev_net = ev_before - commission - slippage
    return round(ev_net / capital_at_risk, 6)


def calibrated_pop(
    payoffs: List[float],
    prices: List[float],
    spot: float,
    iv: float,
    dte: int,
    skew: float = 0.0,
    expected_move: Optional[float] = None,
) -> dict:
    """
    Master PoP computation:
    1. Lognormal PoP with skew adjustment
    2. Fat-tail PoP (t-dist, nu=4)
    3. Expected-move coverage
    Returns dict with all three.
    """
    pop_lognormal  = probability_of_profit(payoffs, prices, spot, iv, dte, skew)
    pop_fat_tail   = fat_tail_pop(payoffs, prices, spot, iv, dte)
    # Weighted blend: 70% lognormal, 30% fat-tail
    pop_blended    = round(0.70*pop_lognormal + 0.30*pop_fat_tail, 4)

    breakevens = _find_breakevens(prices, payoffs)
    pot = probability_of_touch(breakevens, spot, iv, dte)

    em_coverage = None
    if expected_move and expected_move > 0 and breakevens:
        # What fraction of the expected move is outside our profitable zone?
        near = min(breakevens, key=lambda b: abs(b-spot))
        em_coverage = round(abs(near - spot) / expected_move, 4)

    return {
        "pop":          pop_blended,
        "pop_lognormal": pop_lognormal,
        "pop_fat_tail":  pop_fat_tail,
        "pop_touch":     pot,
        "em_coverage":   em_coverage,
    }


def probability_of_loss(
    payoffs: List[float],
    prices: List[float],
    spot: float,
    iv: float,
    dte: int,
    skew: float = 0.0,
) -> float:
    """
    Probability of Loss via direct lognormal integration over loss regions.
    Does NOT compute as (1 - PoP) — integrates independently to avoid
    rounding accumulation and to correctly handle zero-payoff boundary regions.

    skew: put skew (positive = bearish skew); increases PoL for bullish trades.
    """
    if dte <= 0 or iv <= 0 or len(prices) == 0:
        return 0.0
    T = dte / 365.0
    sigma = max(0.05, iv)

    loss_prob = 0.0
    for i in range(len(prices) - 1):
        if payoffs[i] < 0 or payoffs[i + 1] < 0:
            p_lo, p_hi = prices[i], prices[i + 1]
            p_mid = 0.5 * (p_lo + p_hi)
            if p_mid <= 0:
                continue
            z = (math.log(p_mid / spot) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
            pdf = (
                1.0 / (p_mid * sigma * math.sqrt(T) * math.sqrt(2 * math.pi))
            ) * math.exp(-0.5 * z * z)
            if payoffs[i] < 0 and payoffs[i + 1] < 0:
                frac = 1.0
            elif payoffs[i] < 0 and payoffs[i + 1] >= 0:
                frac = abs(payoffs[i]) / (abs(payoffs[i]) + abs(payoffs[i + 1]))
            elif payoffs[i] >= 0 and payoffs[i + 1] < 0:
                frac = abs(payoffs[i + 1]) / (abs(payoffs[i]) + abs(payoffs[i + 1]))
            else:
                frac = 0.0
            loss_prob += pdf * (p_hi - p_lo) * frac

    # Positive skew increases PoL for bullish strategies
    skew_correction = skew * 0.15
    pol = min(0.99, max(0.01, loss_prob + skew_correction))
    return round(pol, 4)


def probability_of_max_loss(
    payoffs: List[float],
    prices: List[float],
    spot: float,
    iv: float,
    dte: int,
    tol_frac: float = 0.02,
) -> float:
    """
    Probability of landing in the max-loss region at expiry.

    Max-loss region is defined as payoff <= min_payoff × (1 - tol_frac),
    i.e., within tol_frac (2%) of the worst-case loss.

    Returns 0 if the strategy has no loss region (max_loss == 0).
    """
    if dte <= 0 or iv <= 0 or len(prices) == 0:
        return 0.0
    min_payoff = min(payoffs)
    if min_payoff >= 0:
        return 0.0
    threshold = min_payoff * (1.0 - tol_frac)   # slightly less negative than min

    T = dte / 365.0
    sigma = max(0.05, iv)
    prob = 0.0
    for i in range(len(prices) - 1):
        if payoffs[i] <= threshold or payoffs[i + 1] <= threshold:
            p_lo, p_hi = prices[i], prices[i + 1]
            p_mid = 0.5 * (p_lo + p_hi)
            if p_mid <= 0:
                continue
            z = (math.log(p_mid / spot) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
            pdf = (
                1.0 / (p_mid * sigma * math.sqrt(T) * math.sqrt(2 * math.pi))
            ) * math.exp(-0.5 * z * z)
            if payoffs[i] <= threshold and payoffs[i + 1] <= threshold:
                frac = 1.0
            elif payoffs[i] <= threshold and payoffs[i + 1] > threshold:
                span = abs(payoffs[i + 1] - payoffs[i])
                frac = abs(payoffs[i] - threshold) / max(span, 1e-12)
            elif payoffs[i] > threshold and payoffs[i + 1] <= threshold:
                span = abs(payoffs[i + 1] - payoffs[i])
                frac = abs(payoffs[i + 1] - threshold) / max(span, 1e-12)
            else:
                frac = 0.0
            prob += pdf * (p_hi - p_lo) * frac
    return round(min(0.99, max(0.0, prob)), 4)


def expected_move(spot: float, iv: float, dte: int) -> float:
    """
    1-standard-deviation expected move at expiry under a lognormal model.

    EM = spot × iv × sqrt(dte / 365)

    Interpretation: 68.27% of lognormal paths end within [spot - EM, spot + EM]
    (approximately — lognormal is asymmetric, but the sigma band is the standard
    convention used by market makers and options analytics platforms).

    Used to calibrate PoP calculations and breakeven coverage ratios.
    """
    if dte <= 0 or iv <= 0 or spot <= 0:
        return 0.0
    T = dte / 365.0
    return round(spot * iv * math.sqrt(T), 4)


def monte_carlo_pop(
    payoffs: List[float],
    prices: List[float],
    spot: float,
    iv: float,
    dte: int,
    n_paths: int = 50_000,
    seed: int = 42,
) -> float:
    """
    Monte Carlo Probability of Profit — cross-check for calibrated_pop().

    Generates n_paths lognormal terminal prices and counts the fraction that
    produce a positive payoff by linearly interpolating the payoff grid.

    seed=42 ensures deterministic output for evidence-chain reproducibility.
    Uses Box-Muller transform (stdlib only, no numpy dependency).
    """
    import random
    if dte <= 0 or iv <= 0 or len(prices) < 2:
        return 0.0
    T = dte / 365.0
    sigma = max(0.05, iv)
    mu = -0.5 * sigma ** 2 * T         # risk-neutral drift (martingale)
    sig_T = sigma * math.sqrt(T)
    p_lo, p_hi = prices[0], prices[-1]

    rng = random.Random(seed)
    profitable = 0
    i_path = 0
    while i_path < n_paths:
        # Box-Muller: two uniforms → one standard normal
        u1 = rng.random()
        u2 = rng.random()
        if u1 < 1e-15:
            u1 = 1e-15
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        price_T = spot * math.exp(mu + sig_T * z)

        # Interpolate payoff from grid
        if price_T <= p_lo:
            pnl = payoffs[0]
        elif price_T >= p_hi:
            pnl = payoffs[-1]
        else:
            lo_i, hi_i = 0, len(prices) - 1
            while hi_i - lo_i > 1:
                mid_i = (lo_i + hi_i) // 2
                if prices[mid_i] <= price_T:
                    lo_i = mid_i
                else:
                    hi_i = mid_i
            frac = (price_T - prices[lo_i]) / max(
                prices[hi_i] - prices[lo_i], 1e-12
            )
            pnl = payoffs[lo_i] + frac * (payoffs[hi_i] - payoffs[lo_i])

        if pnl > 0:
            profitable += 1
        i_path += 1

    return round(profitable / n_paths, 4)
