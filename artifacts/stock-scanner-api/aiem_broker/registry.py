"""Broker adapter registry — SKU-scoped instances (shared market data OK).

Env:
  AIEM_BROKER_PROVIDER = paper | tradier | alpaca | ibkr   (default: paper)
  OE_BROKER_PROVIDER   = same set (falls back to AIEM_BROKER_PROVIDER, then paper)
"""
from __future__ import annotations

import os
from typing import Dict, Tuple

from .base import BrokerAdapter
from .live_gate import live_gate_status
from .paper import PaperBrokerAdapter
from .stubs import STUB_PROVIDERS

try:
    from sku_isolation import normalize_sku, product_name
except Exception:  # pragma: no cover
    def normalize_sku(sku):
        s = (sku or "aiem").strip().lower()
        return s if s in ("aiem", "oe") else "aiem"

    def product_name(sku):
        return "AIEM" if normalize_sku(sku) == "aiem" else "OE"

# Cache key = (provider, sku) so AIEM and OE never share paper cash/positions.
_CACHE: Dict[Tuple[str, str], BrokerAdapter] = {}


def available_providers() -> dict:
    return {
        "paper": {
            "class": "PaperBrokerAdapter",
            "live": False,
            "description": "Simulated fills — current default for AIEM/OE research",
        },
        "tradier": {
            "class": "TradierBrokerStub",
            "live": False,
            "description": "Hookup-ready stub (orders not wired); Tradier already used for market data",
        },
        "alpaca": {
            "class": "AlpacaBrokerStub",
            "live": False,
            "description": "Hookup-ready stub — implement TradingClient later",
        },
        "ibkr": {
            "class": "IbkrBrokerStub",
            "live": False,
            "description": "Hookup-ready stub — implement ib_insync / CPAPI later",
        },
    }


def _provider_env_for_sku(sku: str) -> str:
    sku_n = normalize_sku(sku)
    if sku_n == "oe":
        return (
            os.environ.get("OE_BROKER_PROVIDER")
            or os.environ.get("AIEM_BROKER_PROVIDER")
            or "paper"
        )
    return os.environ.get("AIEM_BROKER_PROVIDER") or "paper"


def get_broker_adapter(provider: str | None = None, sku: str = "aiem") -> BrokerAdapter:
    sku_n = normalize_sku(sku)
    name = (provider or _provider_env_for_sku(sku_n)).strip().lower()
    key = (name, sku_n)
    if key in _CACHE:
        return _CACHE[key]
    if name == "paper":
        adapter: BrokerAdapter = PaperBrokerAdapter(sku=sku_n)
    elif name in STUB_PROVIDERS:
        adapter = STUB_PROVIDERS[name](sku=sku_n)
    else:
        adapter = PaperBrokerAdapter(sku=sku_n)
        name = "paper"
        key = (name, sku_n)
    _CACHE[key] = adapter
    return adapter


def broker_readiness_report(sku: str = "aiem") -> dict:
    sku_n = normalize_sku(sku)
    active = get_broker_adapter(sku=sku_n)
    providers = {}
    for pid, meta in available_providers().items():
        try:
            providers[pid] = get_broker_adapter(pid, sku=sku_n).status()
        except Exception as e:
            providers[pid] = {"provider": pid, "sku": sku_n, "error": str(e)}
    return {
        "sku": sku_n,
        "product": product_name(sku_n),
        "active_provider": active.provider_id,
        "active_status": active.status(),
        "providers": providers,
        "live_gate": live_gate_status(sku_n),
        "shared_market_data": {
            "polygon": "shared OK — quotes / option daily closes only",
            "tradier_spy_bars": "one feed → two evaluate calls (AIEM + OE)",
            "note": "Sharing Polygon cannot cross-submit orders; routing is SKU-tagged",
        },
        "how_to_hookup_later": [
            "1. Keep *_BROKER_PROVIDER=paper until ready",
            "2. Implement place_order() in the chosen stub (replace NOT_IMPLEMENTED)",
            "3. Set broker API credentials in env (separate account IDs per SKU recommended)",
            "4. Arm simulation_lock dual LIVE_TRADING_* flags",
            f"5. Set {'AIEM_ALLOW_LIVE_ORDERS' if sku_n == 'aiem' else 'OE_ALLOW_LIVE_ORDERS'}=1 only after code review",
            "6. Flip provider env for that SKU to tradier|alpaca|ibkr",
            "7. Polygon/Tradier market data may stay shared on the reserved VM",
        ],
    }
