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


def live_orders_permitted() -> bool:
    """True only when simulation_lock is live AND AIEM_ALLOW_LIVE_ORDERS=1."""
    mode = trading_mode()
    allow = os.environ.get("AIEM_ALLOW_LIVE_ORDERS", "") == "1"
    return mode == "live" and allow


def live_order_sent(*, http_order_posted: bool = False) -> bool:
    """Behavioral flag: True only if gate permits AND an HTTP order was posted.

    Paper fill paths MUST pass http_order_posted=False (always returns False).
    Live place_order sets http_order_posted=True only after the request is sent,
    and only if live_orders_permitted() is True — so the flag cannot claim a
    live send while the gate is locked.
    """
    if not http_order_posted:
        return False
    return live_orders_permitted()


def assert_live_orders_allowed(caller: str = "broker.place_order") -> None:
    """Hard stop unless dual live locks are armed AND AIEM_ALLOW_LIVE_ORDERS=1.

    Extra AIEM_ALLOW_LIVE_ORDERS flag is a third deliberate switch so wiring a
    real adapter later still cannot fire accidentally after only env phrase setup.
    """
    if not live_orders_permitted():
        mode = trading_mode()
        allow = os.environ.get("AIEM_ALLOW_LIVE_ORDERS", "") == "1"
        raise LiveOrdersNotAllowed(
            f"[{caller}] Live brokerage orders blocked "
            f"(mode={mode}, AIEM_ALLOW_LIVE_ORDERS={allow}). "
            f"Paper/research mode is the default. To enable later: "
            f"set LIVE_TRADING_* dual locks AND AIEM_ALLOW_LIVE_ORDERS=1, "
            f"then use AIEM_BROKER_PROVIDER=tradier."
        )


def live_gate_status() -> dict:
    mode = trading_mode()
    allow = os.environ.get("AIEM_ALLOW_LIVE_ORDERS", "") == "1"
    permitted = live_orders_permitted()
    return {
        "trading_mode": mode,
        "aiem_allow_live_orders": allow,
        "live_orders_permitted": permitted,
        "live_order_sent_if_posted": live_order_sent(http_order_posted=True),
        "default": "paper",
        "locked": not permitted,
    }
