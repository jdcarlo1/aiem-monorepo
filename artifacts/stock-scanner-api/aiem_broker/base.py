"""Abstract broker adapter — implement this to hook a real broker later."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from .types import BrokerAccount, BrokerPosition, OrderRequest, OrderResult


class BrokerAdapter(ABC):
    """Provider-agnostic brokerage interface.

    Contract:
      - get_quote / get_account / get_positions may be implemented for data.
      - place_order MUST call the live gate before any real broker HTTP call.
      - Stubs return NOT_IMPLEMENTED / BLOCKED — they never send live orders.
    """

    provider_id: str = "base"
    supports_live: bool = False
    supports_options: bool = False

    @abstractmethod
    def status(self) -> dict:
        """Connection / readiness metadata for Sales Readiness UI."""

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        ...

    @abstractmethod
    def get_positions(self) -> List[BrokerPosition]:
        ...

    @abstractmethod
    def get_quote(self, ticker: str) -> Optional[dict]:
        """Return {ticker, last, bid, ask, ...} or None."""

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        """Submit order. Live providers must enforce simulation_lock first."""

    def cancel_order(self, broker_order_id: str) -> OrderResult:
        from .types import OrderStatus
        return OrderResult(
            ok=False,
            status=OrderStatus.NOT_IMPLEMENTED,
            provider=self.provider_id,
            mode="paper",
            ticker="",
            side="",
            quantity=0,
            message=f"{self.provider_id}.cancel_order not implemented",
        )
