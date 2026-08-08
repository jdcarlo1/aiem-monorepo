"""Buying-power checks for paper / pre-live (pure math, no live dependency)."""
from __future__ import annotations

from typing import Any, Dict, Optional


def option_debit_requirement_usd(
    *,
    net_debit_usd: float,
    fees_usd: float = 0.0,
) -> float:
    """Cash/BP required to open a long debit options package."""
    d = max(float(net_debit_usd), 0.0)
    return d + max(float(fees_usd), 0.0)


def cash_secured_put_requirement_usd(strike: float, contracts: float = 1.0) -> float:
    """CSP collateral ≈ strike × 100 × contracts."""
    return abs(float(strike)) * 100.0 * abs(float(contracts))


def check_buying_power(
    *,
    available_bp_usd: float,
    required_usd: float,
    reserved_usd: float = 0.0,
    label: str = "trade",
) -> Dict[str, Any]:
    """
    Return ok/block decision. Never silently allows when BP insufficient.
    """
    avail = float(available_bp_usd) - float(reserved_usd)
    need = float(required_usd)
    ok = avail + 1e-9 >= need and need >= 0
    return {
        "ok": bool(ok),
        "blocked": not bool(ok),
        "label": label,
        "available_bp_usd": round(float(available_bp_usd), 4),
        "reserved_usd": round(float(reserved_usd), 4),
        "free_bp_usd": round(avail, 4),
        "required_usd": round(need, 4),
        "shortfall_usd": round(max(0.0, need - avail), 4),
        "reason": (
            None
            if ok
            else (
                f"INSUFFICIENT_BP: need ${need:.2f} for {label}, "
                f"free ${avail:.2f} (available ${float(available_bp_usd):.2f} "
                f"- reserved ${float(reserved_usd):.2f})"
            )
        ),
    }


def gate_or_raise(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Helper: return decision; callers must not fill when blocked=True."""
    return decision
