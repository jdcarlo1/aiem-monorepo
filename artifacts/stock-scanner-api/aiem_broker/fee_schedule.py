"""Regulatory + commission fee schedule for paper fills.

Joel commission: $0.65 per contract per leg per side (brokerage).
Plus US options regulatory pass-through (approx industry schedule, USD):
  - OCC clearing fee
  - ORF (Options Regulatory Fee) — FINRA
  - TAF (Trading Activity Fee) — FINRA (sells)
  - Exchange fee (conservative blended estimate)

These are rate-table estimates for paper realism — not a brokerage invoice.
"""
from __future__ import annotations

import os
from typing import Any, Dict

# Brokerage commission (Joel / ASE)
COMMISSION_PER_CONTRACT_LEG = float(
    os.environ.get("TRADIER_PAPER_COMMISSION_OPT", "0.65") or 0.65
)

# Regulatory / exchange pass-throughs (per contract, USD). Overridable via env.
OCC_CLEARING_PER_CONTRACT = float(os.environ.get("FEE_OCC_PER_CONTRACT", "0.02") or 0.02)
ORF_PER_CONTRACT = float(os.environ.get("FEE_ORF_PER_CONTRACT", "0.0185") or 0.0185)
# TAF applies on sells (closing long / opening short)
TAF_PER_CONTRACT = float(os.environ.get("FEE_TAF_PER_CONTRACT", "0.00279") or 0.00279)
# Blended exchange fee estimate (varies by exchange; paper uses flat)
EXCHANGE_PER_CONTRACT = float(os.environ.get("FEE_EXCHANGE_PER_CONTRACT", "0.04") or 0.04)


def fee_breakdown(
    *,
    contracts: float,
    n_legs: int = 1,
    side: str = "buy",
    packages: int = 1,
) -> Dict[str, Any]:
    """
    One-way fee breakdown for an options fill.

    contracts = absolute contracts per leg *summed* across legs for the package,
                OR per-leg contracts when n_legs=1.
    For a package, pass total abs contracts across all legs (e.g. fly=4).
    """
    qty = abs(float(contracts)) * max(int(packages), 1)
    # When caller already summed legs into `contracts`, n_legs is informational.
    commission = qty * COMMISSION_PER_CONTRACT_LEG
    occ = qty * OCC_CLEARING_PER_CONTRACT
    orf = qty * ORF_PER_CONTRACT
    exchange = qty * EXCHANGE_PER_CONTRACT
    is_sell = str(side).lower() in ("sell", "sell_to_open", "sell_to_close", "cover_buy_is_not")
    # TAF: charge on sell-side contracts only
    taf = (qty * TAF_PER_CONTRACT) if is_sell else 0.0
    regulatory = occ + orf + taf + exchange
    total = commission + regulatory
    return {
        "contracts": qty,
        "n_legs": int(n_legs),
        "side": side,
        "commission_usd": round(commission, 6),
        "occ_clearing_usd": round(occ, 6),
        "orf_usd": round(orf, 6),
        "taf_usd": round(taf, 6),
        "exchange_usd": round(exchange, 6),
        "regulatory_usd": round(regulatory, 6),
        "total_usd": round(total, 6),
        "schedule": {
            "commission_per_contract_leg": COMMISSION_PER_CONTRACT_LEG,
            "occ_per_contract": OCC_CLEARING_PER_CONTRACT,
            "orf_per_contract": ORF_PER_CONTRACT,
            "taf_per_contract": TAF_PER_CONTRACT,
            "exchange_per_contract": EXCHANGE_PER_CONTRACT,
        },
        "legacy_flat_commission_only_usd": round(commission, 6),
    }


def fee_one_way_total(contracts: float, *, side: str = "buy", n_legs: int = 1) -> float:
    return float(fee_breakdown(contracts=contracts, n_legs=n_legs, side=side)["total_usd"])


def fee_round_trip_total(contracts: float, *, n_legs: int = 1) -> float:
    """Entry buy-side + exit sell-side regulatory (TAF on exit)."""
    entry = fee_one_way_total(contracts, side="buy", n_legs=n_legs)
    exit_ = fee_one_way_total(contracts, side="sell", n_legs=n_legs)
    return round(entry + exit_, 6)
