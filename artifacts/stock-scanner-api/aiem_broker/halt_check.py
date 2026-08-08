"""Trading halt checks before paper fill.

Uses Tradier quote + optional force/historical halt registry.
Paper trading cannot invent live halt feeds — historical fixtures prove the gate.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, Optional

# Publicly documented US equity halt / LULD episodes for forced tests.
# Source: widely reported trading halts (not a live feed).
HISTORICAL_HALTS = {
    # GME extreme volatility — multiple LULD pauses on 2021-01-28
    ("GME", "2021-01-28"): {
        "halted": True,
        "reason": "LULD_VOLATILITY_PAUSE",
        "source": "historical_fixture_public_record",
    },
    ("AMC", "2021-01-28"): {
        "halted": True,
        "reason": "LULD_VOLATILITY_PAUSE",
        "source": "historical_fixture_public_record",
    },
}


def historical_halt(symbol: str, asof: str | date) -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    d = asof.isoformat() if isinstance(asof, date) else str(asof)[:10]
    return HISTORICAL_HALTS.get((sym, d))


def check_halt(
    symbol: str,
    *,
    asof: str | date | None = None,
    quote: Optional[dict] = None,
    force_halted: bool = False,
    force_reason: str = "FORCE_HALT_TEST",
) -> Dict[str, Any]:
    """
    Return {halted, block_fill, reason, source}.
    When halted=True, callers MUST not fill.
    """
    sym = (symbol or "").upper().strip()
    if force_halted:
        return {
            "symbol": sym,
            "halted": True,
            "block_fill": True,
            "reason": force_reason,
            "source": "force_inject",
        }
    if asof:
        hist = historical_halt(sym, asof)
        if hist and hist.get("halted"):
            return {
                "symbol": sym,
                "halted": True,
                "block_fill": True,
                "reason": hist.get("reason"),
                "source": hist.get("source"),
                "asof": str(asof)[:10],
            }
    # Live quote heuristics (Tradier): missing both sides while market open-ish
    if quote:
        bid = quote.get("bid")
        ask = quote.get("ask")
        # Explicit halt flags if present on provider payload
        for k in ("halted", "trading_halted", "is_halted"):
            if quote.get(k) is True:
                return {
                    "symbol": sym,
                    "halted": True,
                    "block_fill": True,
                    "reason": "QUOTE_HALT_FLAG",
                    "source": "tradier_quote_field",
                }
        raw = quote.get("raw") if isinstance(quote.get("raw"), dict) else {}
        if raw.get("halted") is True:
            return {
                "symbol": sym,
                "halted": True,
                "block_fill": True,
                "reason": "RAW_HALT_FLAG",
                "source": "tradier_raw",
            }
    return {
        "symbol": sym,
        "halted": False,
        "block_fill": False,
        "reason": None,
        "source": "clear",
    }


def gate_fill_if_halted(halt: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize gate result for fill path."""
    blocked = bool(halt.get("block_fill") or halt.get("halted"))
    return {
        "ok": not blocked,
        "blocked": blocked,
        "reason": halt.get("reason") if blocked else None,
        "halt": halt,
    }
