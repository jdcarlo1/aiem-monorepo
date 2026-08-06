"""Live-order gate. Real providers MUST call assert_live_orders_allowed()."""
from __future__ import annotations

import os


class LiveOrdersNotAllowed(RuntimeError):
    """Raised when code attempts live brokerage while paper-locked."""


def trading_mode() -> str:
    """Return 'paper' or 'live' based on simulation_lock dual flags."""
    try:
        from simulation_lock import is_live_trading_enabled
        return "live" if is_live_trading_enabled() else "paper"
    except Exception:
        return "paper"


def assert_live_orders_allowed(caller: str = "broker.place_order") -> None:
    """Hard stop unless dual live locks are armed AND AIEM_ALLOW_LIVE_ORDERS=1.

    Extra AIEM_ALLOW_LIVE_ORDERS flag is a third deliberate switch so wiring a
    real adapter later still cannot fire accidentally after only env phrase setup.
    """
    mode = trading_mode()
    allow = os.environ.get("AIEM_ALLOW_LIVE_ORDERS", "") == "1"
    if mode != "live" or not allow:
        raise LiveOrdersNotAllowed(
            f"[{caller}] Live brokerage orders blocked "
            f"(mode={mode}, AIEM_ALLOW_LIVE_ORDERS={allow}). "
            f"Paper/research mode is the default. To enable later: "
            f"set LIVE_TRADING_* dual locks AND AIEM_ALLOW_LIVE_ORDERS=1, "
            f"then implement a real adapter place_order()."
        )


def live_gate_status() -> dict:
    mode = trading_mode()
    allow = os.environ.get("AIEM_ALLOW_LIVE_ORDERS", "") == "1"
    return {
        "trading_mode": mode,
        "aiem_allow_live_orders": allow,
        "live_orders_permitted": mode == "live" and allow,
        "default": "paper",
    }
