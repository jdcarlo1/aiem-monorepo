"""Tradier LIVE brokerage adapter — real order HTTP, gated by live_gate.

NEVER call place_order unless live_gate is armed. Default environment keeps
the gate locked so this module can be imported and readiness-tested without
sending orders.

Uses TRADIER_API_BASE + TRADIER_API_TOKEN(_2) + TRADIER_ACCOUNT_ID.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from .base import BrokerAdapter
from .live_gate import (
    LiveOrdersNotAllowed,
    assert_live_orders_allowed,
    live_gate_status,
    live_order_sent,
)
from .tradier_config import TRADIER_API_BASE
from .tradier_market import (
    _headers,
    connection_probe,
    fetch_quote,
    tradier_account_id,
    tradier_token,
)
from .types import (
    BrokerAccount,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    OrderStatus,
)


class TradierBrokerAdapter(BrokerAdapter):
    """Live Tradier order routing. provider_id = tradier."""

    provider_id = "tradier"
    supports_live = True
    supports_options = True
    uses_live_quotes = True

    def status(self) -> dict:
        probe = connection_probe()
        gate = live_gate_status()
        return {
            "provider": self.provider_id,
            "connected": bool(probe.get("quotes_ok") or probe.get("profile_ok")),
            "supports_live": True,
            "supports_options": True,
            "mode": "live" if gate.get("live_orders_permitted") else "live_locked",
            "order_routing": (
                "enabled" if gate.get("live_orders_permitted") else "blocked_by_live_gate"
            ),
            "api_base": TRADIER_API_BASE,
            "account_id": tradier_account_id() or None,
            "token_present": bool(tradier_token()),
            "live_gate": gate,
            "probe": probe,
            "live_order_sent_default": live_order_sent(http_order_posted=False),
        }

    def get_account(self) -> BrokerAccount:
        tok = tradier_token()
        acct = tradier_account_id()
        cash = None
        bp = None
        details: Dict[str, Any] = {}
        if tok and acct:
            try:
                r = requests.get(
                    f"{TRADIER_API_BASE}/v1/accounts/{acct}/balances",
                    headers=_headers(tok),
                    timeout=10,
                )
                details["balances_http"] = r.status_code
                if r.status_code == 200:
                    bal = (r.json() or {}).get("balances") or {}
                    cash = bal.get("total_cash")
                    margin = bal.get("margin") or {}
                    bp = margin.get("option_buying_power")
                    details["balances"] = bal
            except Exception as e:
                details["balances_error"] = str(e)
        gate = live_gate_status()
        return BrokerAccount(
            provider=self.provider_id,
            account_id=acct or "UNKNOWN",
            currency="USD",
            cash=float(cash) if cash is not None else None,
            buying_power=float(bp) if bp is not None else None,
            mode="live" if gate.get("live_orders_permitted") else "live_locked",
            connected=bool(tok and acct),
            details=details,
        )

    def get_positions(self) -> List[BrokerPosition]:
        tok = tradier_token()
        acct = tradier_account_id()
        if not tok or not acct:
            return []
        try:
            r = requests.get(
                f"{TRADIER_API_BASE}/v1/accounts/{acct}/positions",
                headers=_headers(tok),
                timeout=10,
            )
            if r.status_code != 200:
                return []
            raw = (r.json() or {}).get("positions")
            if not raw or raw == "null":
                return []
            positions = raw.get("position") if isinstance(raw, dict) else raw
            if isinstance(positions, dict):
                positions = [positions]
            out: List[BrokerPosition] = []
            for p in positions or []:
                out.append(
                    BrokerPosition(
                        ticker=str(p.get("symbol") or ""),
                        quantity=float(p.get("quantity") or 0),
                        avg_price=float(p["cost_basis"]) / max(float(p.get("quantity") or 1), 1)
                        if p.get("cost_basis") is not None
                        else None,
                        market_value=p.get("market_value"),
                        asset_class="option" if p.get("symbol", "").startswith("SPY") and len(str(p.get("symbol"))) > 6 else "equity",
                        raw=p,
                    )
                )
            return out
        except Exception:
            return []

    def get_quote(self, ticker: str) -> Optional[dict]:
        return fetch_quote(ticker)

    def get_order(self, broker_order_id: str) -> dict:
        """Read-only order status (allowed while gate locked)."""
        tok = tradier_token()
        acct = tradier_account_id()
        if not tok or not acct:
            return {"ok": False, "error": "missing_token_or_account"}
        url = f"{TRADIER_API_BASE}/v1/accounts/{acct}/orders/{broker_order_id}"
        r = requests.get(url, headers=_headers(tok), timeout=15)
        try:
            body = r.json()
        except Exception:
            body = {"raw_text": r.text}
        return {"ok": r.status_code == 200, "http_status": r.status_code, "body": body, "url": url}

    def cancel_order(self, broker_order_id: str) -> OrderResult:
        """Cancel requires live gate (mutates brokerage state)."""
        try:
            assert_live_orders_allowed(caller=f"{self.provider_id}.cancel_order")
        except LiveOrdersNotAllowed as e:
            return OrderResult(
                ok=False,
                status=OrderStatus.BLOCKED,
                provider=self.provider_id,
                mode="live_blocked",
                ticker="",
                side="cancel",
                quantity=0,
                broker_order_id=str(broker_order_id),
                message=str(e),
                raw={"live_order_sent": live_order_sent(http_order_posted=False)},
            )
        tok = tradier_token()
        acct = tradier_account_id()
        url = f"{TRADIER_API_BASE}/v1/accounts/{acct}/orders/{broker_order_id}"
        r = requests.delete(url, headers=_headers(tok), timeout=15)
        try:
            body = r.json()
        except Exception:
            body = {"raw_text": r.text}
        posted = r.status_code < 500
        return OrderResult(
            ok=r.status_code == 200,
            status=OrderStatus.ACCEPTED if r.status_code == 200 else OrderStatus.REJECTED,
            provider=self.provider_id,
            mode="live",
            ticker="",
            side="cancel",
            quantity=0,
            broker_order_id=str(broker_order_id),
            message=f"cancel http={r.status_code}",
            raw={"http_status": r.status_code, "body": body, "live_order_sent": live_order_sent(http_order_posted=posted)},
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Submit live order — blocked unless live_gate armed."""
        ticker = (order.ticker or "").upper().strip()
        side = order.side.value if hasattr(order.side, "value") else str(order.side)
        qty = float(order.quantity or 0)

        # GATE — must run before any order HTTP. This is the behavior gate.
        try:
            assert_live_orders_allowed(caller=f"{self.provider_id}.place_order")
        except LiveOrdersNotAllowed as e:
            return OrderResult(
                ok=False,
                status=OrderStatus.BLOCKED,
                provider=self.provider_id,
                mode="live_blocked",
                ticker=ticker,
                side=side,
                quantity=qty,
                message=str(e),
                raw={
                    "live_order_sent": live_order_sent(http_order_posted=False),
                    "live_gate": live_gate_status(),
                },
            )

        # Double-check flag helper cannot claim send while gate somehow flipped
        if not live_order_sent(http_order_posted=True):
            return OrderResult(
                ok=False,
                status=OrderStatus.BLOCKED,
                provider=self.provider_id,
                mode="live_blocked",
                ticker=ticker,
                side=side,
                quantity=qty,
                message="live_order_sent() gate returned False — refusing POST",
                raw={"live_order_sent": False, "live_gate": live_gate_status()},
            )

        tok = tradier_token()
        acct = tradier_account_id()
        if not tok or not acct:
            return OrderResult(
                ok=False,
                status=OrderStatus.REJECTED,
                provider=self.provider_id,
                mode="live",
                ticker=ticker,
                side=side,
                quantity=qty,
                message="missing TRADIER_API_TOKEN(_2) or TRADIER_ACCOUNT_ID",
                raw={"live_order_sent": live_order_sent(http_order_posted=False)},
            )

        payload: Dict[str, Any] = {
            "symbol": ticker,
            "side": side if side in (
                "buy", "sell", "buy_to_open", "buy_to_close",
                "sell_to_open", "sell_to_close",
            ) else ("buy" if "buy" in side else "sell"),
            "quantity": str(int(qty) if qty == int(qty) else qty),
            "type": (order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type or "market")),
            "duration": (order.time_in_force.value if hasattr(order.time_in_force, "value") else str(order.time_in_force or "day")),
        }
        if order.asset_class and str(getattr(order.asset_class, "value", order.asset_class)) == "option":
            payload["class"] = "option"
            if order.expiry and order.strike is not None and order.option_right:
                # Prefer explicit option_symbol from metadata when present
                opt_sym = (order.metadata or {}).get("option_symbol")
                if opt_sym:
                    payload["option_symbol"] = opt_sym
                else:
                    payload["option_symbol"] = _occ_symbol(
                        ticker, str(order.expiry)[:10], float(order.strike), str(order.option_right)
                    )
            elif (order.metadata or {}).get("option_symbol"):
                payload["option_symbol"] = order.metadata["option_symbol"]
        else:
            payload["class"] = "equity"

        if payload["type"] == "limit" and order.limit_price is not None:
            payload["price"] = str(order.limit_price)

        url = f"{TRADIER_API_BASE}/v1/accounts/{acct}/orders"
        r = requests.post(url, headers=_headers(tok), data=payload, timeout=20)
        try:
            body = r.json()
        except Exception:
            body = {"raw_text": r.text}

        http_posted = True  # request left this process
        sent_flag = live_order_sent(http_order_posted=http_posted)
        order_obj = body.get("order") if isinstance(body, dict) else None
        oid = str(order_obj.get("id")) if isinstance(order_obj, dict) and order_obj.get("id") is not None else None
        st = str((order_obj or {}).get("status") or "").lower() if isinstance(order_obj, dict) else ""

        if r.status_code >= 400 or not order_obj:
            return OrderResult(
                ok=False,
                status=OrderStatus.REJECTED,
                provider=self.provider_id,
                mode="live",
                ticker=ticker,
                side=side,
                quantity=qty,
                broker_order_id=oid,
                message=f"tradier order rejected http={r.status_code}",
                raw={
                    "http_status": r.status_code,
                    "body": body,
                    "payload": {k: v for k, v in payload.items()},
                    "live_order_sent": sent_flag,
                },
            )

        status = OrderStatus.ACCEPTED
        if st in ("filled", "complete", "completed"):
            status = OrderStatus.FILLED
        elif st in ("partially_filled", "partial"):
            status = OrderStatus.PARTIAL
        elif st in ("rejected", "error"):
            status = OrderStatus.REJECTED

        return OrderResult(
            ok=status != OrderStatus.REJECTED,
            status=status,
            provider=self.provider_id,
            mode="live",
            ticker=ticker,
            side=side,
            quantity=qty,
            fill_price=None,
            broker_order_id=oid,
            message=f"tradier order status={st or 'ok'}",
            raw={
                "http_status": r.status_code,
                "body": body,
                "payload": payload,
                "live_order_sent": sent_flag,
            },
        )


def _occ_symbol(underlying: str, expiry: str, strike: float, right: str) -> str:
    """Build OCC option symbol YYYYMMDD + C/P + strike*1000."""
    y, m, d = expiry.split("-")
    cp = "C" if str(right).lower().startswith("c") else "P"
    strike_i = int(round(float(strike) * 1000))
    return f"{underlying.upper()}{y[2:]}{m}{d}{cp}{strike_i:08d}"
