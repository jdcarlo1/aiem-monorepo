"""Order reject + partial-fill lifecycle for paper / sandbox adapters.

NEVER assumes a fill from a non-success broker response.
Sandbox POSTs that 401/reject must surface status=rejected with raw body.
Partial fills update position qty/avg and P&L on filled qty only.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Canonical Tradier-shaped reject fixtures (documented API error forms).
# Used when sandbox is unreachable (401) so reject *handling* is still tested.
FIXTURE_REJECT_BAD_PRICE = {
    "exception": "invalid_price",
    "error": "Price must be greater than 0 and less than or equal to ask for this order type",
    "http_status": 400,
}
FIXTURE_REJECT_INSUFFICIENT_BP = {
    "errors": {"error": ["Insufficient buying power."]},
    "http_status": 400,
}
FIXTURE_PARTIAL_STATUS = {
    "order": {
        "id": 999001,
        "status": "partially_filled",
        "symbol": "SPY",
        "side": "buy_to_open",
        "quantity": 10,
        "exec_quantity": 4,
        "remaining_quantity": 6,
        "avg_fill_price": 1.25,
        "type": "limit",
        "duration": "day",
        "class": "option",
    }
}
FIXTURE_FILLED_STATUS = {
    "order": {
        "id": 999001,
        "status": "filled",
        "symbol": "SPY",
        "side": "buy_to_open",
        "quantity": 10,
        "exec_quantity": 10,
        "remaining_quantity": 0,
        "avg_fill_price": 1.22,
        "type": "limit",
        "duration": "day",
        "class": "option",
    }
}


def parse_broker_order_response(
    http_status: int,
    body: Any,
    *,
    requested_qty: float,
) -> Dict[str, Any]:
    """
    Map HTTP + body → lifecycle status. NEVER returns filled on error HTTP.
    """
    raw = body
    if isinstance(body, (bytes, bytearray)):
        raw = body.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {"raw_text": raw}

    if int(http_status) in (401, 403):
        return {
            "ok": False,
            "status": "rejected",
            "filled_qty": 0.0,
            "remaining_qty": float(requested_qty),
            "avg_fill_price": None,
            "reason": "AUTH_OR_ACCOUNT_REJECT",
            "http_status": int(http_status),
            "raw": raw,
            "assumed_fill": False,
        }
    if int(http_status) >= 400 or int(http_status) == 0:
        err = None
        if isinstance(raw, dict):
            err = raw.get("error") or raw.get("exception") or raw.get("errors")
        return {
            "ok": False,
            "status": "rejected",
            "filled_qty": 0.0,
            "remaining_qty": float(requested_qty),
            "avg_fill_price": None,
            "reason": str(err or f"HTTP_{http_status}"),
            "http_status": int(http_status),
            "raw": raw,
            "assumed_fill": False,
        }

    order = raw.get("order") if isinstance(raw, dict) else None
    if not isinstance(order, dict):
        # Success HTTP but no order object — do NOT invent a fill
        return {
            "ok": False,
            "status": "rejected",
            "filled_qty": 0.0,
            "remaining_qty": float(requested_qty),
            "avg_fill_price": None,
            "reason": "MISSING_ORDER_OBJECT",
            "http_status": int(http_status),
            "raw": raw,
            "assumed_fill": False,
        }

    st = str(order.get("status") or "").lower()
    exec_qty = float(order.get("exec_quantity") or order.get("filled_quantity") or 0)
    rem = float(
        order.get("remaining_quantity")
        if order.get("remaining_quantity") is not None
        else max(0.0, float(requested_qty) - exec_qty)
    )
    avg = order.get("avg_fill_price") or order.get("price")
    avg_f = float(avg) if avg is not None else None

    if st in ("rejected", "canceled", "cancelled", "expired", "error"):
        return {
            "ok": False,
            "status": "rejected",
            "filled_qty": exec_qty,
            "remaining_qty": rem,
            "avg_fill_price": avg_f,
            "reason": st,
            "http_status": int(http_status),
            "raw": raw,
            "assumed_fill": False,
        }
    if st in ("partially_filled", "partial"):
        return {
            "ok": True,
            "status": "partial",
            "filled_qty": exec_qty,
            "remaining_qty": rem,
            "avg_fill_price": avg_f,
            "reason": None,
            "http_status": int(http_status),
            "raw": raw,
            "assumed_fill": False,
        }
    if st in ("filled", "complete", "completed"):
        return {
            "ok": True,
            "status": "filled",
            "filled_qty": exec_qty if exec_qty > 0 else float(requested_qty),
            "remaining_qty": 0.0,
            "avg_fill_price": avg_f,
            "reason": None,
            "http_status": int(http_status),
            "raw": raw,
            "assumed_fill": False,
        }
    # pending / open / accepted — not a fill
    return {
        "ok": True,
        "status": "accepted",
        "filled_qty": exec_qty,
        "remaining_qty": rem if rem else float(requested_qty),
        "avg_fill_price": avg_f,
        "reason": st or "pending",
        "http_status": int(http_status),
        "raw": raw,
        "assumed_fill": False,
    }


@dataclass
class PartialPosition:
    """Position that correctly accumulates partial fills."""

    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl_usd: float = 0.0
    fills: list = field(default_factory=list)

    def apply_fill(
        self,
        *,
        side: str,
        fill_qty: float,
        fill_price: float,
        multiplier: float = 100.0,
    ) -> Dict[str, Any]:
        """Apply only the filled quantity. Ignores unfilled remainder."""
        q = abs(float(fill_qty))
        px = float(fill_price)
        if q <= 0 or px <= 0:
            return {"ok": False, "reason": "INVALID_FILL", "quantity": self.quantity}
        side_l = side.lower()
        is_buy = side_l in ("buy", "buy_to_open", "buy_to_close")
        pnl = 0.0
        if is_buy:
            # Increase / open long (or cover short)
            if self.quantity < 0:
                cover = min(q, abs(self.quantity))
                pnl = (self.avg_price - px) * cover * multiplier
                self.realized_pnl_usd += pnl
                self.quantity += cover  # toward zero
                q_left = q - cover
                if q_left > 0:
                    self.avg_price = px
                    self.quantity = q_left
            else:
                new_q = self.quantity + q
                if new_q > 0:
                    self.avg_price = (
                        (self.avg_price * self.quantity + px * q) / new_q
                        if self.quantity > 0
                        else px
                    )
                self.quantity = new_q
        else:
            # Sell
            if self.quantity > 0:
                close = min(q, self.quantity)
                pnl = (px - self.avg_price) * close * multiplier
                self.realized_pnl_usd += pnl
                self.quantity -= close
                q_left = q - close
                if q_left > 0:
                    self.avg_price = px
                    self.quantity = -q_left
            else:
                new_q = self.quantity - q  # more short
                # avg for short
                short_before = abs(self.quantity)
                short_after = abs(new_q)
                if short_after > 0:
                    self.avg_price = (
                        (self.avg_price * short_before + px * q) / short_after
                        if short_before > 0
                        else px
                    )
                self.quantity = new_q
        rec = {
            "side": side,
            "fill_qty": q,
            "fill_price": px,
            "pnl_usd": round(pnl, 4),
            "position_qty_after": self.quantity,
            "avg_price_after": self.avg_price,
            "ts": time.time(),
        }
        self.fills.append(rec)
        return {"ok": True, **rec, "realized_pnl_usd": round(self.realized_pnl_usd, 4)}


def poll_order_status_until_terminal(
    fetch_status_fn,
    *,
    order_id: str,
    requested_qty: float,
    max_polls: int = 5,
) -> Dict[str, Any]:
    """
    Poll sandbox-style order status. fetch_status_fn() -> (http_status, body).
    Stops on filled / rejected / partial with remaining 0 / accepted after max.
    """
    history: List[Dict[str, Any]] = []
    last: Dict[str, Any] = {}
    for i in range(max_polls):
        http_status, body = fetch_status_fn(order_id)
        parsed = parse_broker_order_response(
            http_status, body, requested_qty=requested_qty
        )
        parsed["poll"] = i + 1
        history.append(dict(parsed))
        last = parsed
        if parsed["status"] in ("filled", "rejected"):
            break
        if parsed["status"] == "partial":
            break
    out = dict(last)
    out["poll_history"] = history
    return out


def new_client_order_id(prefix: str = "aiem") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
