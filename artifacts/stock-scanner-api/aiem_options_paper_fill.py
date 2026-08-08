"""
Autonomous OE paper fill helpers.

Policy (paper only — live broker path stays locked elsewhere):
  * Long option buys fill at the **ask** (conservative; no mid fantasy).
  * One-sided quotes (synth bid) → fill at ask, label ONE_SIDED_ASK.
  * Slippage stored in **dollars** (half-spread × 100 × qty).
  * Fees default $0.65/contract round-trip estimate.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple


DEFAULT_FEES_PER_CONTRACT = 0.65


def paper_buy_fill(
    bid: Any,
    ask: Any,
    *,
    one_sided: bool = False,
) -> Tuple[Optional[float], str]:
    """Return (fill_price_per_share, fill_quality) for a long paper buy."""
    try:
        a = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        a = None
    if a is None or a <= 0:
        return None, "NO_ASK"
    quality = "ONE_SIDED_ASK" if one_sided else "ASK"
    return round(a, 4), quality


def paper_slippage_dollars(
    bid: Any,
    ask: Any,
    quantity: int = 1,
) -> float:
    """Half-spread in dollars for quantity contracts (×100 multiplier)."""
    try:
        b = float(bid) if bid is not None else 0.0
        a = float(ask) if ask is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if a <= 0:
        return 0.0
    if b <= 0:
        # One-sided: treat full ask as uncertainty band → half ask * 100
        half = a * 0.5
    else:
        half = max(0.0, (a - b) / 2.0)
    qty = max(1, int(quantity or 1))
    return round(half * 100.0 * qty, 4)


def paper_realized_pnl(
    *,
    entry_price: float,
    exit_price: float,
    quantity: int = 1,
    fees_est: float = 0.0,
    slippage_est: float = 0.0,
    side: str = "BUY",
) -> Tuple[float, Optional[float]]:
    """
    Dollar P&L and return_pct for a single-leg option paper trade.
    Prices are per-share premium; multiplier 100.
    """
    qty = max(1, int(quantity or 1))
    if side.upper() == "BUY":
        per_share = float(exit_price) - float(entry_price)
    else:
        per_share = float(entry_price) - float(exit_price)
    pnl = round(per_share * 100.0 * qty - float(fees_est or 0) - float(slippage_est or 0), 4)
    ret = round(per_share / float(entry_price), 6) if float(entry_price) > 0 else None
    return pnl, ret
