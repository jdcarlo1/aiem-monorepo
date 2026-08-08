"""Multi-leg package pricing — atomic package fill (not independent legging).

Real spread orders fill as one net debit/credit. This module:
  - Computes the package NATURAL (long@ask / short@bid) as the AON fill
  - Exposes a single package_fill_price + fill_id
  - Does not permit partial leg fills (all-or-none at package level)
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .paper_fills import (
    COMMISSION_PER_CONTRACT_LEG,
    LegSpec,
    package_contract_count,
    price_option_leg_nbbo,
)
from .fee_schedule import fee_breakdown

try:
    from .halt_check import check_halt, gate_fill_if_halted
except Exception:  # pragma: no cover
    check_halt = None  # type: ignore
    gate_fill_if_halted = None  # type: ignore


def _leg_mid(leg: dict) -> Optional[float]:
    bid, ask = leg.get("bid"), leg.get("ask")
    if bid and ask:
        return (float(bid) + float(ask)) / 2.0
    return float(leg["premium"]) if leg.get("premium") else None


def price_package_atomic(
    underlying: str,
    expiration: date | str,
    legs: Sequence[LegSpec],
    *,
    packages: int = 1,
    for_exit: bool = False,
    include_fees: bool = True,
    check_halts: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Price multi-leg as ONE package fill (AON).

    BEFORE (legging model): each leg independently filled; sum notionals.
    AFTER (package model): same NBBO natural math, but emitted as a single
    package ticket — one fill_id, one net package_fill_price, aon=True.
    If any leg quote missing → entire package rejected (no partial legging).
    """
    und = (underlying or "").upper().strip()
    if check_halts and check_halt is not None:
        halt = check_halt(und)
        gate = gate_fill_if_halted(halt)
        if gate["blocked"]:
            return {
                "ok": False,
                "status": "blocked_halt",
                "reason": gate["reason"],
                "halt": halt,
                "live_order_sent": False,
                "assumed_fill": False,
            }

    exp = expiration if isinstance(expiration, date) else date.fromisoformat(str(expiration)[:10])
    priced_legs: List[dict] = []
    natural_usd = 0.0  # signed; debit > 0
    mid_usd = 0.0
    for qty, right, strike in legs:
        leg = price_option_leg_nbbo(
            und, float(strike), exp, right, int(qty), for_exit=for_exit
        )
        if not leg:
            return {
                "ok": False,
                "status": "rejected",
                "reason": f"MISSING_LEG_QUOTE {right} {strike}",
                "live_order_sent": False,
                "assumed_fill": False,
                "aon": True,
            }
        natural_usd += int(qty) * float(leg["premium"]) * 100.0
        mid = _leg_mid(leg)
        if mid is None:
            return {
                "ok": False,
                "status": "rejected",
                "reason": f"MISSING_LEG_MID {right} {strike}",
                "live_order_sent": False,
                "assumed_fill": False,
                "aon": True,
            }
        mid_usd += int(qty) * float(mid) * 100.0
        priced_legs.append(leg)

    pkgs = max(int(packages), 1)
    package_natural_usd = natural_usd * pkgs
    package_mid_usd = mid_usd * pkgs
    # Single package fill price (debit per share equivalent)
    package_fill_price = package_natural_usd / (pkgs * 100.0)

    contracts = package_contract_count(legs, pkgs)
    n_legs = len(list(legs))
    side = "buy" if package_natural_usd >= 0 else "sell"
    fees = (
        fee_breakdown(contracts=contracts, n_legs=n_legs, side=side, packages=1)
        if include_fees
        else None
    )
    fees_usd = float(fees["total_usd"]) if fees else 0.0
    # Legacy commission-only (old model)
    legacy_commission = contracts * COMMISSION_PER_CONTRACT_LEG

    fill_id = f"pkg-{uuid.uuid4().hex[:12]}"
    if for_exit:
        net_after_fees = package_natural_usd - fees_usd
    else:
        net_after_fees = package_natural_usd + fees_usd

    return {
        "ok": True,
        "status": "filled",
        "aon": True,
        "legging_allowed": False,
        "fill_id": fill_id,
        "underlying": und,
        "expiration": exp.isoformat(),
        "packages": pkgs,
        "legs": priced_legs,  # detail only — not independently fillable
        "package_fill_price": round(package_fill_price, 6),
        "net_usd": round(package_natural_usd, 4),
        "mid_usd": round(package_mid_usd, 4),
        "package_edge_vs_mid_usd": round(package_natural_usd - package_mid_usd, 4),
        "fees_usd": round(fees_usd, 4),
        "fees_breakdown": fees,
        "legacy_commission_only_usd": round(legacy_commission, 4),
        "net_usd_after_fees": round(net_after_fees, 4),
        "debit_per_share": round(package_fill_price, 6),
        "contract_count": contracts,
        "n_legs": n_legs,
        "pricing_source": "tradier_nbbo_package_aon",
        "for_exit": bool(for_exit),
        "live_order_sent": False,
        "simulated": True,
        "assumed_fill": False,
    }
