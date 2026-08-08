"""AIEM broker adapter layer — Tradier paper fills now, live stubs later."""

from .live_gate import LiveOrdersNotAllowed, assert_live_orders_allowed, live_gate_status, trading_mode
from .registry import (
    available_providers,
    broker_readiness_report,
    clear_broker_cache,
    default_provider_name,
    get_broker_adapter,
)
from .paper_fills import (
    COMMISSION_PER_CONTRACT_LEG,
    fee_one_way,
    fee_round_trip,
    price_package_nbbo,
    price_single_option_nbbo,
)
from .tradier_paper import TradierPaperBrokerAdapter
from .types import (
    AssetClass,
    OrderLeg,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

__all__ = [
    "AssetClass",
    "COMMISSION_PER_CONTRACT_LEG",
    "LiveOrdersNotAllowed",
    "OrderLeg",
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
    "fee_one_way",
    "fee_round_trip",
    "get_broker_adapter",
    "live_gate_status",
    "price_package_nbbo",
    "price_single_option_nbbo",
    "trading_mode",
]
