"""Broker adapter layer — paper works; tradier live blocked unless gated."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_paper_adapter_simulates_fill():
    from aiem_broker import OrderRequest, OrderSide, OrderStatus
    from aiem_broker.paper import PaperBrokerAdapter

    adapter = PaperBrokerAdapter(starting_cash=100_000.0)
    res = adapter.place_order(
        OrderRequest(
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=10,
            metadata={"ref_price": 190.0},
        )
    )
    assert res.ok is True
    assert res.status == OrderStatus.SIMULATED
    assert res.fill_price == 190.0
    assert res.provider == "paper"
    assert res.mode == "paper"
    acct = adapter.get_account()
    assert acct.connected is True
    assert acct.cash is not None and abs(acct.cash - (100_000.0 - 1900.0)) < 0.01


def test_tradier_live_blocks_without_live_arm():
    from aiem_broker import OrderRequest, OrderSide, OrderStatus, clear_broker_cache, get_broker_adapter

    # Ensure live not armed
    os.environ.pop("LIVE_TRADING_ENABLED", None)
    os.environ.pop("LIVE_TRADING_CONFIRMATION_PHRASE", None)
    os.environ.pop("LIVE_TRADING_EXPECTED_PHRASE", None)
    os.environ.pop("AIEM_ALLOW_LIVE_ORDERS", None)
    clear_broker_cache()
    adapter = get_broker_adapter("tradier")
    assert adapter.__class__.__name__ == "TradierBrokerAdapter"
    res = adapter.place_order(
        OrderRequest(ticker="MSFT", side=OrderSide.BUY, quantity=1, metadata={"ref_price": 400})
    )
    assert res.ok is False
    assert res.status in (OrderStatus.BLOCKED, OrderStatus.NOT_IMPLEMENTED)
    assert "No order was sent" in res.message or "blocked" in res.message.lower() or "Live brokerage" in res.message
    assert (res.raw or {}).get("live_order_sent") is False


def test_broker_readiness_report_lists_providers():
    from aiem_broker import broker_readiness_report, clear_broker_cache

    clear_broker_cache()
    rep = broker_readiness_report()
    assert rep["active_provider"] in ("paper", "tradier_paper", "tradier", "alpaca", "ibkr")
    assert "paper" in rep["providers"]
    assert "tradier" in rep["providers"]
    assert "tradier_paper" in rep["providers"]
    assert rep["live_gate"]["live_orders_permitted"] is False
    assert len(rep.get("how_to_go_live_later") or rep.get("how_to_hookup_later") or []) >= 5
