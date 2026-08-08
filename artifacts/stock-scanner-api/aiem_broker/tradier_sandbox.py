"""Tradier sandbox order adapter — surfaces rejects; never assumes fill.

Current brokerage token returns 401 on sandbox.tradier.com. This adapter still
POSTs when configured, parses the raw response, and refuses to invent fills.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

from .order_lifecycle import parse_broker_order_response
from .tradier_market import TRADIER_API_BASE, tradier_account_id, tradier_token, _headers

SANDBOX_BASE = (os.environ.get("TRADIER_SANDBOX_BASE") or "https://sandbox.tradier.com").rstrip("/")


def sandbox_post_order(payload: Dict[str, Any], *, token: Optional[str] = None) -> Dict[str, Any]:
    """
    POST /v1/accounts/{id}/orders on sandbox.
    Returns parsed lifecycle dict. assumed_fill is ALWAYS False on errors.
    """
    tok = token or tradier_token()
    acct = tradier_account_id()
    qty = float(payload.get("quantity") or 0)
    if not tok or not acct:
        return parse_broker_order_response(
            0,
            {"error": "missing_token_or_account"},
            requested_qty=qty,
        )
    url = f"{SANDBOX_BASE}/v1/accounts/{acct}/orders"
    try:
        r = requests.post(
            url,
            data=payload,
            headers=_headers(tok),
            timeout=15,
        )
        body: Any
        try:
            body = r.json()
        except Exception:
            body = r.text
        parsed = parse_broker_order_response(r.status_code, body, requested_qty=qty)
        parsed["url"] = url
        parsed["request_payload"] = {k: v for k, v in payload.items() if k != "token"}
        # HARD RULE: never upgrade to filled on auth failure
        if r.status_code >= 400:
            assert parsed["status"] == "rejected"
            assert parsed["filled_qty"] == 0.0
            assert parsed["assumed_fill"] is False
        return parsed
    except Exception as e:
        return parse_broker_order_response(
            0, {"error": str(e)}, requested_qty=qty
        )


def sandbox_get_order(order_id: str, *, token: Optional[str] = None) -> Dict[str, Any]:
    tok = token or tradier_token()
    acct = tradier_account_id()
    url = f"{SANDBOX_BASE}/v1/accounts/{acct}/orders/{order_id}"
    try:
        r = requests.get(url, headers=_headers(tok), timeout=15)
        try:
            body = r.json()
        except Exception:
            body = r.text
        return {
            "http_status": r.status_code,
            "body": body,
            "url": url,
        }
    except Exception as e:
        return {"http_status": 0, "body": {"error": str(e)}, "url": url}


def prod_base_note() -> str:
    return f"prod_base={TRADIER_API_BASE} sandbox_base={SANDBOX_BASE}"
