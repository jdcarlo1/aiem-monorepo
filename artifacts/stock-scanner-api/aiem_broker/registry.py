"""Broker adapter registry.

Env:
  AIEM_BROKER_PROVIDER = paper | tradier_paper | tradier | alpaca | ibkr
  Default: tradier_paper when TRADIER_API_TOKEN(_2) is set, else paper.
"""
from __future__ import annotations

import os
from typing import Dict

from .base import BrokerAdapter
from .live_gate import live_gate_status
from .paper import PaperBrokerAdapter
from .stubs import STUB_PROVIDERS
from .tradier_market import tradier_token
from .tradier_paper import TradierPaperBrokerAdapter

_CACHE: Dict[str, BrokerAdapter] = {}


def available_providers() -> dict:
    return {
        "paper": {
            "class": "PaperBrokerAdapter",
            "live": False,
            "description": "Simulated fills without live quotes",
        },
        "tradier_paper": {
            "class": "TradierPaperBrokerAdapter",
            "live": False,
            "description": (
                "Paper fills at live Tradier bid/ask — brokerage feel, "
                "no live orders sent"
            ),
        },
        "tradier": {
            "class": "TradierBrokerStub",
            "live": False,
            "description": "Live-order stub (NOT wired); keep tradier_paper for research",
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


def default_provider_name() -> str:
    env = (os.environ.get("AIEM_BROKER_PROVIDER") or "").strip().lower()
    if env:
        return env
    # Auto-select Tradier paper when a token is present so strategies feel live.
    if tradier_token():
        return "tradier_paper"
    return "paper"


def get_broker_adapter(provider: str | None = None) -> BrokerAdapter:
    name = (provider or default_provider_name()).strip().lower()
    if name in _CACHE:
        return _CACHE[name]
    if name == "tradier_paper":
        adapter: BrokerAdapter = TradierPaperBrokerAdapter()
    elif name == "paper":
        adapter = PaperBrokerAdapter()
    elif name in STUB_PROVIDERS:
        adapter = STUB_PROVIDERS[name]()
    else:
        adapter = TradierPaperBrokerAdapter() if tradier_token() else PaperBrokerAdapter()
        name = adapter.provider_id
    _CACHE[name] = adapter
    return adapter


def clear_broker_cache() -> None:
    _CACHE.clear()


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
        "default_provider": default_provider_name(),
        "providers": providers,
        "live_gate": live_gate_status(),
        "how_to_hookup_later": [
            "1. Keep AIEM_BROKER_PROVIDER=tradier_paper for brokerage-like paper fills",
            "2. Set TRADIER_API_TOKEN_2 + TRADIER_ACCOUNT_ID for live quotes / identity",
            "3. Live money orders still require a separate live adapter (tradier stub)",
            "4. Arm simulation_lock dual LIVE_TRADING_* flags only for live path",
            "5. Set AIEM_ALLOW_LIVE_ORDERS=1 only after code review",
            "6. Flip AIEM_BROKER_PROVIDER to tradier only after place_order is implemented",
        ],
    }
