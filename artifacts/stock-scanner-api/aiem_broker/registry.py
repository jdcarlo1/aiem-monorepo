"""Broker adapter registry.

Env:
  AIEM_BROKER_PROVIDER = paper | tradier | alpaca | ibkr   (default: paper)
"""
from __future__ import annotations

import os
from typing import Dict

from .base import BrokerAdapter
from .live_gate import live_gate_status
from .paper import PaperBrokerAdapter
from .stubs import STUB_PROVIDERS

_CACHE: Dict[str, BrokerAdapter] = {}


def available_providers() -> dict:
    return {
        "paper": {
            "class": "PaperBrokerAdapter",
            "live": False,
            "description": "Simulated fills — current default for AIEM research",
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


def get_broker_adapter(provider: str | None = None) -> BrokerAdapter:
    name = (provider or os.environ.get("AIEM_BROKER_PROVIDER") or "paper").strip().lower()
    if name in _CACHE:
        return _CACHE[name]
    if name == "paper":
        adapter: BrokerAdapter = PaperBrokerAdapter()
    elif name in STUB_PROVIDERS:
        adapter = STUB_PROVIDERS[name]()
    else:
        adapter = PaperBrokerAdapter()
        name = "paper"
    _CACHE[name] = adapter
    return adapter


def broker_readiness_report() -> dict:
    active = get_broker_adapter()
    providers = {}
    for pid, meta in available_providers().items():
        try:
            providers[pid] = get_broker_adapter(pid).status()
        except Exception as e:
            providers[pid] = {"provider": pid, "error": str(e)}
    return {
        "active_provider": active.provider_id,
        "active_status": active.status(),
        "providers": providers,
        "live_gate": live_gate_status(),
        "how_to_hookup_later": [
            "1. Keep AIEM_BROKER_PROVIDER=paper until ready",
            "2. Implement place_order() in the chosen stub (replace NOT_IMPLEMENTED)",
            "3. Set broker API credentials in env",
            "4. Arm simulation_lock dual LIVE_TRADING_* flags",
            "5. Set AIEM_ALLOW_LIVE_ORDERS=1 only after code review",
            "6. Flip AIEM_BROKER_PROVIDER to tradier|alpaca|ibkr",
        ],
    }
