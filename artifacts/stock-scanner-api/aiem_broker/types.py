"""Shared broker adapter types. Provider-agnostic."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    BUY_TO_OPEN = "buy_to_open"
    SELL_TO_CLOSE = "sell_to_close"


class AssetClass(str, Enum):
    EQUITY = "equity"
    OPTION = "option"
    ETF = "etf"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"


class OrderStatus(str, Enum):
    SIMULATED = "simulated"
    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    NOT_IMPLEMENTED = "not_implemented"


@dataclass
class OrderRequest:
    ticker: str
    side: OrderSide
    quantity: float
    asset_class: AssetClass = AssetClass.EQUITY
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Optional[float] = None
    # Option legs (optional)
    strike: Optional[float] = None
    expiry: Optional[str] = None  # YYYY-MM-DD
    option_right: Optional[str] = None  # call|put
    client_order_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Enum):
                d[k] = v.value
        return d


@dataclass
class OrderResult:
    ok: bool
    status: OrderStatus
    provider: str
    mode: str  # paper | live_blocked | live
    ticker: str
    side: str
    quantity: float
    fill_price: Optional[float] = None
    broker_order_id: Optional[str] = None
    message: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d.get("status"), Enum):
            d["status"] = d["status"].value
        return d


@dataclass
class BrokerAccount:
    provider: str
    account_id: str
    currency: str = "USD"
    cash: Optional[float] = None
    buying_power: Optional[float] = None
    mode: str = "paper"
    connected: bool = False
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BrokerPosition:
    ticker: str
    quantity: float
    avg_price: Optional[float] = None
    market_value: Optional[float] = None
    asset_class: str = "equity"
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
