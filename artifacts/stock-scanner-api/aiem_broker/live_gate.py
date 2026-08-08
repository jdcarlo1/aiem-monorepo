"""Live-order gate. Real providers MUST call assert_live_orders_allowed()."""
from __future__ import annotations

import os

try:
    from sku_isolation import live_allow_env_key, normalize_sku, product_name
except Exception:  # pragma: no cover
    def normalize_sku(sku):
        s = (sku or "aiem").strip().lower()
        return s if s in ("aiem", "oe") else "aiem"

    def product_name(sku):
        return "AIEM" if normalize_sku(sku) == "aiem" else "OE"

    def live_allow_env_key(sku):
        return "AIEM_ALLOW_LIVE_ORDERS" if normalize_sku(sku) == "aiem" else "OE_ALLOW_LIVE_ORDERS"


class LiveOrdersNotAllowed(RuntimeError):
    """Raised when code attempts live brokerage while paper-locked."""


def trading_mode() -> str:
    """Return 'paper' or 'live' based on simulation_lock dual flags."""
    try:
        from simulation_lock import is_live_trading_enabled
        return "live" if is_live_trading_enabled() else "paper"
    except Exception:
        return "paper"


def assert_live_orders_allowed(caller: str = "broker.place_order", sku: str = "aiem") -> None:
    """Hard stop unless dual live locks are armed AND the SKU allow flag is 1.

    Per-SKU third switch:
      AIEM → AIEM_ALLOW_LIVE_ORDERS=1
      OE   → OE_ALLOW_LIVE_ORDERS=1
    So arming one product cannot unlock the other.
    """
    sku_n = normalize_sku(sku)
    mode = trading_mode()
    flag = live_allow_env_key(sku_n)
    allow = os.environ.get(flag, "") == "1"
    if mode != "live" or not allow:
        raise LiveOrdersNotAllowed(
            f"[{caller}] Live brokerage orders blocked for sku={sku_n} "
            f"(mode={mode}, {flag}={allow}). "
            f"Paper/research mode is the default. Market data (Polygon) may be shared; "
            f"order routing is SKU-isolated. To enable later: set LIVE_TRADING_* dual locks "
            f"AND {flag}=1, then implement a real adapter place_order()."
        )


def live_gate_status(sku: str = "aiem") -> dict:
    sku_n = normalize_sku(sku)
    mode = trading_mode()
    flag = live_allow_env_key(sku_n)
    allow = os.environ.get(flag, "") == "1"
    return {
        "sku": sku_n,
        "product": product_name(sku_n),
        "trading_mode": mode,
        "allow_live_orders_env": flag,
        "allow_live_orders": allow,
        # Backward-compatible key (AIEM historical)
        "aiem_allow_live_orders": os.environ.get("AIEM_ALLOW_LIVE_ORDERS", "") == "1",
        "oe_allow_live_orders": os.environ.get("OE_ALLOW_LIVE_ORDERS", "") == "1",
        "live_orders_permitted": mode == "live" and allow,
        "default": "paper",
        "shared_market_data_ok": True,
        "order_routing_isolated": True,
    }
