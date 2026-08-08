"""
Broker stubs — ready to hook up later, never place live orders today.

Each stub:
  - advertises credentials / endpoints needed for a future implementation
  - can optionally read market data if already available in-process
  - place_order ALWAYS returns NOT_IMPLEMENTED / BLOCKED (no HTTP order calls)
"""
from __future__ import annotations

import os
from typing import List, Optional

from .base import BrokerAdapter
from .live_gate import LiveOrdersNotAllowed, assert_live_orders_allowed, live_gate_status
from .types import (
    BrokerAccount,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    OrderStatus,
)


class _StubBroker(BrokerAdapter):
    provider_id = "stub"
    supports_live = True  # interface ready; implementation not wired
    supports_options = False
    env_keys: tuple = ()
    docs_hookup: str = ""

    def _creds_present(self) -> dict:
        return {k: bool(os.environ.get(k)) for k in self.env_keys}

    def status(self) -> dict:
        creds = self._creds_present()
        return {
            "provider": self.provider_id,
            "connected": False,
            "ready_for_live_hookup": all(creds.values()) if creds else False,
            "credentials_present": creds,
            "supports_live": True,
            "supports_options": self.supports_options,
            "mode": "stub",
            "order_routing": "not_wired",
            "hookup_notes": self.docs_hookup,
            "live_gate": live_gate_status(),
        }

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            provider=self.provider_id,
            account_id="NOT_CONNECTED",
            mode="stub",
            connected=False,
            details={"credentials_present": self._creds_present()},
        )

    def get_positions(self) -> List[BrokerPosition]:
        return []

    def get_quote(self, ticker: str) -> Optional[dict]:
        return {
            "ticker": (ticker or "").upper(),
            "last": None,
            "source": f"{self.provider_id}_stub",
            "note": "Quote path not wired — use Tradier market-data helpers until hookup",
        }

    def place_order(self, order: OrderRequest) -> OrderResult:
        # Even if someone arms live locks, stubs refuse until a real impl replaces this.
        try:
            assert_live_orders_allowed(caller=f"{self.provider_id}.place_order")
        except LiveOrdersNotAllowed as e:
            return OrderResult(
                ok=False,
                status=OrderStatus.BLOCKED,
                provider=self.provider_id,
                mode="live_blocked",
                ticker=(order.ticker or "").upper(),
                side=order.side.value,
                quantity=float(order.quantity or 0),
                message=str(e),
            )
        return OrderResult(
            ok=False,
            status=OrderStatus.NOT_IMPLEMENTED,
            provider=self.provider_id,
            mode="stub",
            ticker=(order.ticker or "").upper(),
            side=order.side.value,
            quantity=float(order.quantity or 0),
            message=(
                f"{self.provider_id} adapter is a hookup stub — "
                f"implement place_order() against the broker API when ready. "
                f"No order was sent."
            ),
        )


class TradierBrokerStub(_StubBroker):
    """Tradier LIVE brokerage orders — NOT wired.

    For brokerage-like paper fills use AIEM_BROKER_PROVIDER=tradier_paper
    (TradierPaperBrokerAdapter) which quotes live NBBO but never POSTs orders.
    """
    provider_id = "tradier"
    supports_options = True
    env_keys = ("TRADIER_API_TOKEN_2", "TRADIER_ACCOUNT_ID")
    docs_hookup = (
        "LIVE ONLY: Implement POST /v1/accounts/{account_id}/orders using TRADIER_API_TOKEN_2. "
        "Paper path is already live via tradier_paper (quotes only, no order HTTP). "
        "Call assert_live_orders_allowed() first. Never confuse with tradier_paper."
    )


class AlpacaBrokerStub(_StubBroker):
    provider_id = "alpaca"
    supports_options = False
    env_keys = ("ALPACA_API_KEY", "ALPACA_API_SECRET", "ALPACA_BASE_URL")
    docs_hookup = (
        "Implement alpaca-py TradingClient submit_order. "
        "Use paper URL first (https://paper-api.alpaca.markets) before live."
    )


class IbkrBrokerStub(_StubBroker):
    provider_id = "ibkr"
    supports_options = True
    env_keys = ("IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID")
    docs_hookup = (
        "Implement ib_insync / Client Portal Web API. "
        "Require paper account gateway before any live account id."
    )


STUB_PROVIDERS = {
    "tradier": TradierBrokerStub,
    "alpaca": AlpacaBrokerStub,
    "ibkr": IbkrBrokerStub,
}
