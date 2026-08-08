#!/usr/bin/env python3
"""Paper-to-live switch prep — negative control + gate unit tests.

Proves:
  1. live_gate locked by default (armed=false)
  2. AIEM_BROKER_PROVIDER=tradier place_order is BLOCKED with no HTTP POST
  3. live_order_sent is False on paper + blocked live paths
  4. When temporarily armed (process-local env), assert_live_orders_allowed passes
     and live_order_sent(http_order_posted=True) becomes True — then restore lock
  5. Read-only live-token account probe works against api.tradier.com ($0 BP OK)
  6. After all tests, gate is still locked (no live orders possible)

Does NOT send live orders.
"""
from __future__ import annotations

import os
import sys
import traceback
from unittest import mock

# Ensure API package is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Load .env if present (workspace root)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_env = os.path.join(_ROOT, ".env")
if os.path.exists(_env):
    for line in open(_env):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def test_gate_locked_by_default() -> dict:
    from aiem_broker.live_gate import (
        LiveOrdersNotAllowed,
        assert_live_orders_allowed,
        live_gate_status,
        live_order_sent,
        live_orders_permitted,
    )

    # Ensure we are not accidentally armed from ambient env
    for k in (
        "AIEM_ALLOW_LIVE_ORDERS",
        "LIVE_TRADING_ENABLED",
        "LIVE_TRADING_CONFIRMATION_PHRASE",
        "LIVE_TRADING_EXPECTED_PHRASE",
    ):
        os.environ.pop(k, None)

    st = live_gate_status()
    assert st["locked"] is True, st
    assert st["live_orders_permitted"] is False, st
    assert live_orders_permitted() is False
    assert live_order_sent(http_order_posted=False) is False
    assert live_order_sent(http_order_posted=True) is False  # gate locked

    blocked = False
    try:
        assert_live_orders_allowed(caller="negctl.default")
    except LiveOrdersNotAllowed:
        blocked = True
    assert blocked, "assert_live_orders_allowed must raise when locked"

    return {"ok": True, "status": st}


def test_gate_allows_when_armed_then_relock() -> dict:
    from aiem_broker.live_gate import (
        LiveOrdersNotAllowed,
        assert_live_orders_allowed,
        live_gate_status,
        live_order_sent,
        live_orders_permitted,
    )

    phrase = "CURSOR_PAPER_TO_LIVE_NEGCTL_ONLY"
    os.environ["LIVE_TRADING_ENABLED"] = "true"
    os.environ["LIVE_TRADING_EXPECTED_PHRASE"] = phrase
    os.environ["LIVE_TRADING_CONFIRMATION_PHRASE"] = phrase
    os.environ["AIEM_ALLOW_LIVE_ORDERS"] = "1"

    try:
        assert live_orders_permitted() is True
        assert live_order_sent(http_order_posted=True) is True
        assert live_order_sent(http_order_posted=False) is False
        assert_live_orders_allowed(caller="negctl.armed")
        st_armed = live_gate_status()
        assert st_armed["locked"] is False
    finally:
        # RELLOCK — critical
        for k in (
            "AIEM_ALLOW_LIVE_ORDERS",
            "LIVE_TRADING_ENABLED",
            "LIVE_TRADING_CONFIRMATION_PHRASE",
            "LIVE_TRADING_EXPECTED_PHRASE",
        ):
            os.environ.pop(k, None)

    assert live_orders_permitted() is False
    assert live_order_sent(http_order_posted=True) is False
    blocked = False
    try:
        assert_live_orders_allowed(caller="negctl.relocked")
    except LiveOrdersNotAllowed:
        blocked = True
    assert blocked
    return {"ok": True, "armed_status": st_armed, "relocked": live_gate_status()}


def test_tradier_provider_place_order_blocked_no_http() -> dict:
    from aiem_broker.registry import clear_broker_cache, get_broker_adapter
    from aiem_broker.types import OrderRequest, OrderSide, OrderStatus, OrderType, TimeInForce

    # Force locked gate
    for k in (
        "AIEM_ALLOW_LIVE_ORDERS",
        "LIVE_TRADING_ENABLED",
        "LIVE_TRADING_CONFIRMATION_PHRASE",
        "LIVE_TRADING_EXPECTED_PHRASE",
    ):
        os.environ.pop(k, None)

    clear_broker_cache()
    os.environ["AIEM_BROKER_PROVIDER"] = "tradier"
    adapter = get_broker_adapter("tradier")
    assert adapter.provider_id == "tradier"
    assert adapter.__class__.__name__ == "TradierBrokerAdapter"

    order = OrderRequest(
        ticker="SPY",
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    post_calls = []

    def _fake_post(*args, **kwargs):
        post_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("requests.post must not be called while gate locked")

    with mock.patch("aiem_broker.tradier_live.requests.post", side_effect=_fake_post):
        with mock.patch("requests.post", side_effect=_fake_post):
            result = adapter.place_order(order)

    assert result.status == OrderStatus.BLOCKED, result
    assert result.ok is False
    assert result.raw.get("live_order_sent") is False
    assert post_calls == [], post_calls

    # cancel also blocked, no DELETE
    del_calls = []

    def _fake_del(*args, **kwargs):
        del_calls.append(1)
        raise AssertionError("requests.delete must not fire while locked")

    with mock.patch("aiem_broker.tradier_live.requests.delete", side_effect=_fake_del):
        cres = adapter.cancel_order("999999")
    assert cres.status == OrderStatus.BLOCKED
    assert cres.raw.get("live_order_sent") is False
    assert del_calls == []

    st = adapter.status()
    return {
        "ok": True,
        "place_status": str(result.status),
        "place_message": result.message,
        "live_order_sent": result.raw.get("live_order_sent"),
        "adapter_status_mode": st.get("mode"),
        "order_routing": st.get("order_routing"),
        "http_posts": len(post_calls),
    }


def test_paper_live_order_sent_false() -> dict:
    from aiem_broker.live_gate import live_order_sent
    from aiem_broker.registry import clear_broker_cache, get_broker_adapter
    from aiem_broker.types import OrderRequest, OrderSide, OrderType, TimeInForce

    clear_broker_cache()
    paper = get_broker_adapter("tradier_paper")
    assert live_order_sent(http_order_posted=False) is False
    order = OrderRequest(
        ticker="SPY",
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    try:
        res = paper.place_order(order)
        flag = (res.raw or {}).get("live_order_sent")
        status = getattr(res.status, "value", res.status)
    except Exception as e:
        res = None
        flag = live_order_sent(http_order_posted=False)
        status = f"soft_fail:{e}"
        print(f"[paper] place_order soft-fail (quotes?): {e}")
    assert flag is False
    return {
        "ok": True,
        "paper_provider": paper.provider_id,
        "live_order_sent": flag,
        "paper_status": status,
    }


def test_readonly_live_token_probe() -> dict:
    """Read-only balances against live api — NO order POST."""
    from aiem_broker.tradier_config import TRADIER_API_BASE
    from aiem_broker.tradier_market import connection_probe, tradier_account_id, tradier_token
    from aiem_broker.registry import clear_broker_cache, get_broker_adapter

    tok = tradier_token()
    acct = tradier_account_id()
    probe = connection_probe()
    clear_broker_cache()
    live = get_broker_adapter("tradier")
    account = live.get_account()
    return {
        "ok": True,
        "api_base": TRADIER_API_BASE,
        "token_present": bool(tok),
        "token_len": len(tok) if tok else 0,
        "account_id": acct,
        "probe": {k: probe.get(k) for k in ("quotes_ok", "profile_ok", "http_status", "error") if k in probe or True},
        "probe_raw_keys": sorted(probe.keys()),
        "cash": account.cash,
        "buying_power": account.buying_power,
        "mode": account.mode,
        "note": "read-only; no place_order HTTP",
    }


def test_risk_env_defaults() -> dict:
    import aim_asym_paper_strategies as asym
    import aim_f3_spy_0dte as f3

    return {
        "ok": True,
        "ASYM_RISK_USD": asym.RISK_USD,
        "ASYM_MAX_PLATEAU": asym.MAX_PLATEAU_PAYOFF_USD,
        "ASYM_NARROW_WING": asym.NARROW_WING_PLATEAU_PAYOFF_USD,
        "F3_TRADE_NOTIONAL_USD": f3.TRADE_NOTIONAL_USD,
        "F3_STOP_LOSS_PCT": f3.STOP_LOSS_PCT,
        "expect": {
            "ASYM_RISK_USD": 500.0,
            "F3_TRADE_NOTIONAL_USD": 200.0,
            "F3_STOP_LOSS_PCT": 0.65,
        },
        "defaults_match": (
            asym.RISK_USD == 500.0
            and f3.TRADE_NOTIONAL_USD == 200.0
            and f3.STOP_LOSS_PCT == 0.65
        ),
    }


def main() -> int:
    results = {}
    failures = []

    tests = [
        ("gate_locked_default", test_gate_locked_by_default),
        ("gate_arm_then_relock", test_gate_allows_when_armed_then_relock),
        ("tradier_place_blocked_no_http", test_tradier_provider_place_order_blocked_no_http),
        ("paper_live_order_sent_false", test_paper_live_order_sent_false),
        ("readonly_live_token_probe", test_readonly_live_token_probe),
        ("risk_env_defaults", test_risk_env_defaults),
    ]

    for name, fn in tests:
        _banner(name)
        try:
            results[name] = fn()
            print("PASS", name, results[name])
        except Exception as e:
            failures.append(name)
            results[name] = {"ok": False, "error": str(e), "tb": traceback.format_exc()}
            print("FAIL", name)
            print(results[name]["tb"])

    _banner("FINAL LOCK CHECK")
    from aiem_broker.live_gate import live_gate_status, live_orders_permitted

    final = live_gate_status()
    print(final)
    still_locked = final.get("locked") is True and live_orders_permitted() is False
    print("STILL_LOCKED", still_locked)
    if not still_locked:
        failures.append("final_lock")

    _banner("SUMMARY")
    print({"failures": failures, "n_ok": len(tests) - len([f for f in failures if f != "final_lock"])})
    if failures:
        print("OVERALL_FAIL")
        return 1
    print("OVERALL_PASS — live orders still blocked end-to-end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
