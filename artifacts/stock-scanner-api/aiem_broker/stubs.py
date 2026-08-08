"""
Broker stubs — Alpaca / IBKR remain stubs. Tradier live is in tradier_live.py.
"""
from __future__ import annotations

import os
from typing import List, Optional

from .base import BrokerAdapter
from .live_gate import LiveOrdersNotAllowed, assert_live_orders_allowed, live_gate_status, live_order_sent
from .types import (
    BrokerAccount,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    OrderStatus,
)


class _StubBroker(BrokerAdapter):
    provider_id = "stub"
    supports_live = True
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
            "live_order_sent": live_order_sent(http_order_posted=False),
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
            "note": "Quote path not wired",
        }

    def place_order(self, order: OrderRequest) -> OrderResult:
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
                raw={"live_order_sent": live_order_sent(http_order_posted=False)},
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
            raw={"live_order_sent": live_order_sent(http_order_posted=False)},
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


# Tradier is no longer a stub — see tradier_live.TradierBrokerAdapter
STUB_PROVIDERS = {
    "alpaca": AlpacaBrokerStub,
    "ibkr": IbkrBrokerStub,
}
