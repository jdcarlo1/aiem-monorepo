"""Paper broker adapter — simulates fills; never talks to a real broker."""
from __future__ import annotations

import time
import uuid
from typing import List, Optional

from .base import BrokerAdapter
from .types import (
    BrokerAccount,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    OrderStatus,
)


class PaperBrokerAdapter(BrokerAdapter):
    provider_id = "paper"
    supports_live = False
    supports_options = True

    def __init__(self, starting_cash: float = 100_000.0):
        self._cash = float(starting_cash)
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: list[dict] = []

    def status(self) -> dict:
        return {
            "provider": self.provider_id,
            "connected": True,
            "ready_for_live_hookup": False,
            "supports_live": False,
            "supports_options": True,
            "mode": "paper",
            "note": "Active paper simulator. Swap AIEM_BROKER_PROVIDER to a stub/live provider later.",
        }

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            provider=self.provider_id,
            account_id="AIEM-PAPER",
            cash=self._cash,
            buying_power=self._cash,
            mode="paper",
            connected=True,
        )

    def get_positions(self) -> List[BrokerPosition]:
        return list(self._positions.values())

    def get_quote(self, ticker: str) -> Optional[dict]:
        # Avoid importing main.py (circular). Callers may pass ref_price in metadata
        # on place_order; quotes for the paper adapter are optional.
        return {
            "ticker": ticker.upper(),
            "last": None,
            "source": "paper_adapter",
            "note": "Inject ref_price via OrderRequest.metadata or wire a quote_fn later",
        }

    def place_order(self, order: OrderRequest) -> OrderResult:
        ticker = (order.ticker or "").upper()
        qty = float(order.quantity or 0)
        if not ticker or qty <= 0:
            return OrderResult(
                ok=False,
                status=OrderStatus.REJECTED,
                provider=self.provider_id,
                mode="paper",
                ticker=ticker,
                side=order.side.value,
                quantity=qty,
                message="invalid ticker/quantity",
            )

        quote = self.get_quote(ticker) or {}
        px = quote.get("last") or order.limit_price
        if px is None:
            # Deterministic paper fallback so research loops never hard-fail.
            px = float(order.metadata.get("ref_price") or 100.0)

        fill = float(px)
        # Simple cash/position bookkeeping for adapter demos (not the aiem_paper_trades ledger).
        notional = fill * qty
        side = order.side.value
        if side in ("buy", "buy_to_open"):
            self._cash -= notional
            prev = self._positions.get(ticker)
            if prev:
                new_qty = prev.quantity + qty
                avg = ((prev.avg_price or fill) * prev.quantity + fill * qty) / max(new_qty, 1e-9)
                self._positions[ticker] = BrokerPosition(ticker, new_qty, avg, new_qty * fill)
            else:
                self._positions[ticker] = BrokerPosition(ticker, qty, fill, notional)
        else:
            self._cash += notional
            prev = self._positions.get(ticker)
            if prev:
                left = prev.quantity - qty
                if left <= 1e-9:
                    self._positions.pop(ticker, None)
                else:
                    self._positions[ticker] = BrokerPosition(
                        ticker, left, prev.avg_price, left * fill
                    )

        oid = f"PAPER-{uuid.uuid4().hex[:12]}"
        result = OrderResult(
            ok=True,
            status=OrderStatus.SIMULATED,
            provider=self.provider_id,
            mode="paper",
            ticker=ticker,
            side=side,
            quantity=qty,
            fill_price=round(fill, 4),
            broker_order_id=oid,
            message="simulated paper fill — no broker contacted",
            raw={"ts": time.time(), "quote": quote},
        )
        self._orders.append(result.to_dict())
        return result
