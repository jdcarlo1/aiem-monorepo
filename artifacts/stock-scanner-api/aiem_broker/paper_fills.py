"""Shared live-Tradier → paper-fill helpers for all Joel strategies.

NEVER places live brokerage orders. Uses Tradier NBBO (buy→ask, sell→bid)
and Joel fee schedule: $0.65 per contract per leg per side.

Round-trip fees for a package:
  fees_rt = 2 * sum(|leg_qty|) * packages * COMMISSION_PER_CONTRACT_LEG
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .tradier_market import fetch_option_quote, fetch_quote

# Joel / ASE schedule (align with aiem_strat_engine.config.COMMISSION_PER_LEG)
COMMISSION_PER_CONTRACT_LEG = float(
    os.environ.get("TRADIER_PAPER_COMMISSION_OPT", "0.65") or 0.65
)

LegSpec = Tuple[int, str, float]  # (qty_signed, 'call'|'put', strike)


def fee_one_way(total_contracts: float) -> float:
    """Commission for one side (entry OR exit) across all contracts."""
    return abs(float(total_contracts)) * COMMISSION_PER_CONTRACT_LEG


def fee_round_trip(total_contracts: float) -> float:
    """Entry + exit commission for the same contract count."""
    return 2.0 * fee_one_way(total_contracts)


def package_contract_count(legs: Sequence[LegSpec] | Sequence[dict], packages: int = 1) -> float:
    """Sum of absolute contracts across legs × packages."""
    n = 0.0
    for leg in legs:
        if isinstance(leg, dict):
            n += abs(float(leg.get("qty") or leg.get("quantity") or 0))
        else:
            n += abs(float(leg[0]))
    return n * max(int(packages), 1)


def _fill_side_for_qty(qty_signed: int, *, for_exit: bool = False) -> str:
    """
    Long (+qty): entry buy@ask, exit sell@bid.
    Short (-qty): entry sell@bid, exit buy@ask (cover).
    """
    q = int(qty_signed)
    if not for_exit:
        return "buy" if q > 0 else "sell"
    return "sell" if q > 0 else "buy"


def price_option_leg_nbbo(
    underlying: str,
    strike: float,
    expiry: str | date,
    right: str,
    qty_signed: int,
    *,
    for_exit: bool = False,
) -> Optional[dict]:
    """Resolve one option leg fill from live Tradier NBBO. Returns None if no quote."""
    exp = expiry.isoformat() if isinstance(expiry, date) else str(expiry)[:10]
    q = fetch_option_quote(underlying, float(strike), exp, right=right)
    if not q:
        return None
    bid, ask = q.get("bid"), q.get("ask")
    mid = q.get("mid")
    last = q.get("last")
    side = _fill_side_for_qty(qty_signed, for_exit=for_exit)
    if side == "buy":
        fill = ask or mid or last or bid
    else:
        fill = bid or mid or last or ask
    if fill is None or float(fill) <= 0:
        return None
    half_spread = None
    if bid and ask and mid:
        half_spread = (float(ask) - float(bid)) / 2.0
    return {
        "qty": int(qty_signed),
        "right": str(right).lower(),
        "strike": float(q.get("strike") or strike),
        "premium": float(fill),
        "fill_price": float(fill),
        "bid": float(bid) if bid else None,
        "ask": float(ask) if ask else None,
        "mid": float(mid) if mid else None,
        "half_spread": half_spread,
        "option_symbol": q.get("option_symbol"),
        "symbol": q.get("option_symbol"),
        "fill_side": side,
        "for_exit": bool(for_exit),
        "source": "tradier_option_nbbo",
        "expiry": exp,
    }


def price_package_nbbo(
    underlying: str,
    expiration: date | str,
    legs: Sequence[LegSpec],
    *,
    packages: int = 1,
    for_exit: bool = False,
    include_fees: bool = True,
) -> Optional[dict]:
    """
    Price a multi-leg package at live Tradier NBBO.

    Returns dollars for `packages` lots:
      net_usd  — signed package value (debit > 0, credit < 0) BEFORE fees
      fees_usd — one-way commission for this fill side
      debit_per_share — net_usd / (packages * 100) before fees
    """
    exp = expiration if isinstance(expiration, date) else date.fromisoformat(str(expiration)[:10])
    priced_legs: List[dict] = []
    total_usd = 0.0
    for qty, right, strike in legs:
        leg = price_option_leg_nbbo(
            underlying, float(strike), exp, right, int(qty), for_exit=for_exit
        )
        if not leg:
            return None
        total_usd += int(qty) * float(leg["premium"]) * 100.0
        priced_legs.append(leg)

    pkgs = max(int(packages), 1)
    net_usd = total_usd * pkgs
    contracts = package_contract_count(legs, pkgs)
    fees = fee_one_way(contracts) if include_fees else 0.0

    # Debit entry pays fees on top; credit entry nets fees against credit received.
    # Exit long package: proceeds = net_usd (sell) - fees.
    if for_exit:
        # For long packages net_usd at exit is what we'd receive if flat (same sign convention
        # as entry mark). PnL = exit_net - entry_net - entry_fees - exit_fees (caller).
        net_after_fees = net_usd - fees
    else:
        # Entry: cash outlay increases by fees for debits; credits reduced by fees.
        if net_usd >= 0:
            net_after_fees = net_usd + fees
        else:
            net_after_fees = net_usd + fees  # e.g. -200 + 2.60 = -197.40 credit received

    return {
        "ok": True,
        "underlying": underlying.upper(),
        "expiration": exp.isoformat(),
        "packages": pkgs,
        "legs": priced_legs,
        "net_usd": round(net_usd, 4),
        "fees_usd": round(fees, 4),
        "net_usd_after_fees": round(net_after_fees, 4),
        "debit_per_share": round(net_usd / (pkgs * 100.0), 6),
        "contract_count": contracts,
        "commission_per_contract_leg": COMMISSION_PER_CONTRACT_LEG,
        "pricing_source": "tradier_nbbo_paper",
        "for_exit": bool(for_exit),
        "live_order_sent": False,
        "simulated": True,
    }


def price_single_option_nbbo(
    underlying: str,
    strike: float,
    expiry: str | date,
    right: str,
    *,
    quantity: float = 1.0,
    is_buy: bool = True,
    for_exit: bool = False,
) -> Optional[dict]:
    """Single-leg helper used by F3 / OE / AIEM long calls."""
    qty_signed = int(quantity) if quantity == int(quantity) else (1 if is_buy else -1)
    # For fractional F3 contracts, price 1 lot then scale.
    leg_qty = 1 if (is_buy and not for_exit) or (not is_buy and for_exit) else -1
    if for_exit:
        leg_qty = -1 if is_buy else 1  # closing a long → sell; closing a short → buy
    else:
        leg_qty = 1 if is_buy else -1

    leg = price_option_leg_nbbo(
        underlying, strike, expiry, right, leg_qty, for_exit=for_exit
    )
    if not leg:
        return None
    qty = abs(float(quantity))
    fees = fee_one_way(qty)
    prem = float(leg["premium"])
    notional = prem * qty * 100.0
    return {
        **leg,
        "quantity": qty,
        "premium": prem,
        "notional_usd": round(notional, 4),
        "fees_usd": round(fees, 4),
        "notional_after_fees_usd": round(
            notional + fees if (is_buy and not for_exit) else notional - fees, 4
        ),
        "pricing_source": "tradier_nbbo_paper",
        "live_order_sent": False,
        "simulated": True,
    }


def equity_nbbo_fill(ticker: str, *, is_buy: bool = True) -> Optional[dict]:
    q = fetch_quote(ticker)
    if not q:
        return None
    bid, ask, last = q.get("bid"), q.get("ask"), q.get("last")
    mid = None
    if bid and ask:
        mid = (float(bid) + float(ask)) / 2.0
    fill = (ask or last or bid) if is_buy else (bid or last or ask)
    if fill is None or float(fill) <= 0:
        return None
    half = ((float(ask) - float(bid)) / 2.0) if bid and ask else None
    return {
        "ticker": ticker.upper(),
        "fill_price": float(fill),
        "bid": float(bid) if bid else None,
        "ask": float(ask) if ask else None,
        "mid": mid,
        "half_spread": half,
        "source": "tradier_equity_nbbo",
        "live_order_sent": False,
        "simulated": True,
    }
