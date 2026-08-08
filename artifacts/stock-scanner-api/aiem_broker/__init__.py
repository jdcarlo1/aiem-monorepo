"""AIEM broker adapter layer — Tradier paper fills now, live stubs later."""

from .live_gate import LiveOrdersNotAllowed, assert_live_orders_allowed, live_gate_status, trading_mode
from .registry import (
    available_providers,
    broker_readiness_report,
    clear_broker_cache,
    default_provider_name,
    get_broker_adapter,
)
from .tradier_paper import TradierPaperBrokerAdapter
from .types import (
    AssetClass,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

__all__ = [
    "AssetClass",
    "LiveOrdersNotAllowed",
    "OrderRequest",
    "OrderResult",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "TimeInForce",
    "TradierPaperBrokerAdapter",
    "assert_live_orders_allowed",
    "available_providers",
    "broker_readiness_report",
    "clear_broker_cache",
    "default_provider_name",
    "get_broker_adapter",
    "live_gate_status",
    "trading_mode",
]
