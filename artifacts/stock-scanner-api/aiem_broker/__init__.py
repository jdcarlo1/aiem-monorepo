"""AIEM broker adapter layer — paper now, hookup-ready stubs for later."""

from .live_gate import LiveOrdersNotAllowed, assert_live_orders_allowed, live_gate_status, trading_mode
from .registry import available_providers, broker_readiness_report, get_broker_adapter
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
    "assert_live_orders_allowed",
    "available_providers",
    "broker_readiness_report",
    "get_broker_adapter",
    "live_gate_status",
    "trading_mode",
]
