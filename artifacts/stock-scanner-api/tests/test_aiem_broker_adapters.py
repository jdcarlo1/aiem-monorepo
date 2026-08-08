"""Broker adapter layer — paper works; stubs never send live orders."""
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


def test_tradier_stub_blocks_without_live_arm():
    from aiem_broker import OrderRequest, OrderSide, OrderStatus, get_broker_adapter

    # Ensure live not armed
    os.environ.pop("LIVE_TRADING_ENABLED", None)
    os.environ.pop("AIEM_ALLOW_LIVE_ORDERS", None)
    stub = get_broker_adapter("tradier")
    res = stub.place_order(
        OrderRequest(ticker="MSFT", side=OrderSide.BUY, quantity=1, metadata={"ref_price": 400})
    )
    assert res.ok is False
    assert res.status in (OrderStatus.BLOCKED, OrderStatus.NOT_IMPLEMENTED)
    assert "No order was sent" in res.message or "blocked" in res.message.lower() or "Live brokerage" in res.message


def test_broker_readiness_report_lists_providers():
    from aiem_broker import broker_readiness_report

    rep = broker_readiness_report(sku="aiem")
    assert rep["sku"] == "aiem"
    assert rep["active_provider"] in ("paper", "tradier", "alpaca", "ibkr")
    assert "paper" in rep["providers"]
    assert "tradier" in rep["providers"]
    assert rep["live_gate"]["live_orders_permitted"] is False
    assert len(rep["how_to_hookup_later"]) >= 5
    assert "polygon" in rep["shared_market_data"]


def test_sku_broker_adapters_are_isolated():
    from aiem_broker import OrderRequest, OrderSide, get_broker_adapter

    aiem = get_broker_adapter("paper", sku="aiem")
    oe = get_broker_adapter("paper", sku="oe")
    assert aiem is not oe
    aiem.place_order(
        OrderRequest(ticker="AAA", side=OrderSide.BUY, quantity=2, metadata={"ref_price": 50})
    )
    assert aiem.get_account().cash != oe.get_account().cash
    assert aiem.get_account().account_id.startswith("AIEM")
    assert oe.get_account().account_id.startswith("OE")


def test_oe_live_flag_does_not_unlock_aiem():
    import os
    from aiem_broker.live_gate import assert_live_orders_allowed, LiveOrdersNotAllowed

    os.environ["OE_ALLOW_LIVE_ORDERS"] = "1"
    os.environ.pop("AIEM_ALLOW_LIVE_ORDERS", None)
    os.environ.pop("LIVE_TRADING_ENABLED", None)
    try:
        try:
            assert_live_orders_allowed(sku="aiem")
            raised = False
        except LiveOrdersNotAllowed:
            raised = True
        assert raised is True
    finally:
        os.environ.pop("OE_ALLOW_LIVE_ORDERS", None)
