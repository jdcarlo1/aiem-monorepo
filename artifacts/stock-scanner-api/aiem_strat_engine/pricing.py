"""
pricing.py — Fill price estimation, slippage modeling, and commission calculation.
"""
from __future__ import annotations
from typing import List, Optional
from .legs import Leg, SIDE_LONG, SIDE_SHORT, ASSET_STOCK
from .config import (
    COMMISSION_PER_LEG, COMMISSION_BASE_TRADE,
    REG_FEE_PER_CONTRACT, OCC_FER_CLEARING_FEE,
    DEFAULT_SLIPPAGE_FRAC,
)


def mid_price(legs: List[Leg]) -> Optional[float]:
    """
    Net mid-market price (positive = debit, negative = credit).
    Returns None if any leg is missing bid/ask.
    """
    total = 0.0
    for lg in legs:
        if lg.mid is None:
            return None
        sign = 1 if lg.side == SIDE_LONG else -1
        total += sign * lg.mid * lg.ratio
    return round(total, 4)


def conservative_fill(legs: List[Leg]) -> Optional[float]:
    """
    Conservative executable price:
    - Long legs: filled at ask (pay more)
    - Short legs: filled at bid (receive less)
    This models realistic multi-leg fill execution worst-case.
    """
    total = 0.0
    for lg in legs:
        if lg.bid is None or lg.ask is None:
            return None
        if lg.side == SIDE_LONG:
            price = lg.ask
        else:
            price = lg.bid
        sign = 1 if lg.side == SIDE_LONG else -1
        total += sign * price * lg.ratio
    return round(total, 4)


def bid_ask_spread_fraction(legs: List[Leg]) -> Optional[float]:
    """
    Effective bid-ask spread as a fraction of mid price for the whole structure.
    Measures fill quality / liquidity cost.
    """
    total_spread = 0.0
    total_mid    = 0.0
    for lg in legs:
        if lg.bid is None or lg.ask is None or lg.mid is None or lg.mid == 0:
            return None
        spread_frac = (lg.ask - lg.bid) / max(lg.mid, 0.01)
        total_spread += spread_frac * lg.ratio
        total_mid    += abs(lg.mid) * lg.ratio
    if total_mid == 0:
        return None
    # Weighted average spread fraction
    return round(total_spread / len(legs), 4)


def slippage_estimate(legs: List[Leg], underlying_vol: float = 0.30) -> float:
    """
    Estimate expected slippage cost per contract in dollars.
    Primary driver: bid-ask spread of each leg.
    Secondary driver: underlying volatility (wider in high-vol).

    Returns total slippage for all legs combined (positive = cost).
    """
    total_slip = 0.0
    for lg in legs:
        if lg.bid is None or lg.ask is None:
            slip = DEFAULT_SLIPPAGE_FRAC * (lg.mid or 1.0)
        else:
            spread = max(0.0, lg.ask - lg.bid)
            # Expect to fill at mid + 10-25% of spread depending on vol
            vol_factor = min(0.25, max(0.10, underlying_vol / 3.0))
            slip = spread * vol_factor
        # Stock legs: slippage is smaller (cents)
        if lg.asset_type == ASSET_STOCK:
            slip = min(slip, 0.05)
        total_slip += slip * lg.ratio * 100  # per contract (100 shares)
    return round(total_slip, 4)


def commission(legs: List[Leg], contracts: int = 1) -> float:
    """
    Total commission for entering the position.
    contracts: number of spread units (e.g. 1 = 1 iron condor = 4 legs × 1 contract each).
    """
    option_legs = [lg for lg in legs if lg.asset_type != ASSET_STOCK]
    n_legs = sum(lg.ratio for lg in option_legs)
    per_leg = (COMMISSION_PER_LEG + REG_FEE_PER_CONTRACT + OCC_FER_CLEARING_FEE) * contracts
    total = COMMISSION_BASE_TRADE + n_legs * per_leg
    return round(total, 4)


def fill_quality_score(legs: List[Leg]) -> float:
    """
    Normalized fill quality score in [0, 1].
    1.0 = tight spreads, high liquidity.
    0.0 = untradeable (crosses or extreme widths).
    """
    scores = []
    for lg in legs:
        if lg.bid is None or lg.ask is None or lg.ask == 0:
            scores.append(0.0)
            continue
        if lg.bid >= lg.ask:
            scores.append(0.0)  # crossed quote
            continue
        spread_frac = (lg.ask - lg.bid) / max(lg.mid or lg.ask, 0.01)
        # Penalize wide spreads: 5% = excellent, 30% = poor
        score = max(0.0, 1.0 - spread_frac / 0.30)
        # Also reward high volume and OI
        oi_bonus = min(0.10, (lg.open_interest or 0) / 10000 * 0.10)
        scores.append(min(1.0, score + oi_bonus))
    return round(sum(scores) / max(len(scores), 1), 4) if scores else 0.0


def liquidity_score(legs: List[Leg]) -> float:
    """
    Combined liquidity score considering OI, volume, and bid-ask spread.
    Returns value in [0, 1].
    """
    scores = []
    for lg in legs:
        oi   = lg.open_interest or 0
        vol  = lg.volume or 0
        bid  = lg.bid or 0
        ask  = lg.ask or 0
        mid  = lg.mid or 0.01

        oi_score  = min(1.0, oi  / 1000.0)
        vol_score = min(1.0, vol / 200.0)
        if bid >= ask or mid == 0:
            spread_score = 0.0
        else:
            spread_score = max(0.0, 1.0 - (ask-bid)/mid/0.30)

        leg_score = (oi_score * 0.4 + vol_score * 0.3 + spread_score * 0.3)
        scores.append(leg_score)
    return round(sum(scores) / max(len(scores), 1), 4) if scores else 0.0
