#!/usr/bin/env python3
"""Verify AIEM vs OE have the same option patterns but isolated SKU books."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from aim_asym_paper_strategies import STRATEGY_KEYS, SHARED_OPTIONS_PATTERN_KEYS
from aim_paper_trading_engine import AIMPaperTradingEngine


def main() -> int:
    aiem = AIMPaperTradingEngine(sku="aiem")
    oe = AIMPaperTradingEngine(sku="oe")
    aiem_snap = aiem.dashboard_snapshot()
    oe_snap = oe.options_snapshot()

    print("STRATEGY_KEYS", STRATEGY_KEYS)
    print("SHARED_OPTIONS_PATTERN_KEYS", SHARED_OPTIONS_PATTERN_KEYS)
    print("AIEM_sku", aiem_snap.get("sku"), "product", aiem_snap.get("product"))
    print("OE_sku", oe_snap.get("sku"), "product", oe_snap.get("product"))

    fail = 0

    def ok(name: str, cond: bool, detail: str = "") -> None:
        nonlocal fail
        print(("PASS" if cond else "FAIL"), name, detail)
        if not cond:
            fail += 1

    ok("aiem_sku", aiem.sku == "aiem" and aiem_snap.get("sku") == "aiem")
    ok("oe_sku", oe.sku == "oe" and oe_snap.get("sku") == "oe")
    ok("engines_are_distinct_objects", aiem is not oe)
    ok("aiem_has_equity", "gap_fill" in aiem_snap and "orb" in aiem_snap)
    ok("oe_no_equity", "gap_fill" not in oe_snap and "orb" not in oe_snap)

    for k in SHARED_OPTIONS_PATTERN_KEYS:
        ok(f"aiem_has_{k}", k in aiem_snap)
        ok(f"oe_has_{k}", k in oe_snap)

    for k in STRATEGY_KEYS:
        ok(f"aiem_{k}_sku_tag", (aiem_snap.get(k) or {}).get("sku") == "aiem")
        ok(f"oe_{k}_sku_tag", (oe_snap.get(k) or {}).get("sku") == "oe")
        ok(
            f"{k}_independent_balance",
            aiem.asym[k].account_balance_usd == oe.asym[k].account_balance_usd
            and aiem.asym[k] is not oe.asym[k],
        )

    # Same pattern set
    aiem_opts = {k for k in aiem_snap if k in SHARED_OPTIONS_PATTERN_KEYS}
    oe_opts = {k for k in oe_snap if k in SHARED_OPTIONS_PATTERN_KEYS}
    ok("same_option_pattern_keys", aiem_opts == oe_opts == set(SHARED_OPTIONS_PATTERN_KEYS))

    # F3 SKU tags
    if aiem.f3 is not None and oe.f3 is not None:
        ok("f3_engines_distinct", aiem.f3 is not oe.f3)
        ok("aiem_f3_sku", getattr(aiem.f3, "sku", None) == "aiem")
        ok("oe_f3_sku", getattr(oe.f3, "sku", None) == "oe")
        ok("aiem_f3_snap_sku", (aiem_snap.get("f3") or {}).get("sku") == "aiem")
        ok("oe_f3_snap_sku", (oe_snap.get("f3") or {}).get("sku") == "oe")

    # Broker adapters are SKU-isolated (separate paper cash books)
    from aiem_broker import get_broker_adapter, broker_readiness_report
    from aiem_broker import OrderRequest, OrderSide

    aiem_br = get_broker_adapter("paper", sku="aiem")
    oe_br = get_broker_adapter("paper", sku="oe")
    ok("broker_adapters_distinct", aiem_br is not oe_br)
    aiem_br.place_order(
        OrderRequest(ticker="TEST", side=OrderSide.BUY, quantity=1, metadata={"ref_price": 10})
    )
    ok(
        "broker_cash_isolated",
        abs(float(aiem_br.get_account().cash) - float(oe_br.get_account().cash)) > 1.0,
    )
    aiem_rep = broker_readiness_report(sku="aiem")
    oe_rep = broker_readiness_report(sku="oe")
    ok("aiem_broker_report_sku", aiem_rep.get("sku") == "aiem")
    ok("oe_broker_report_sku", oe_rep.get("sku") == "oe")
    ok("live_blocked_aiem", aiem_rep["live_gate"]["live_orders_permitted"] is False)
    ok("live_blocked_oe", oe_rep["live_gate"]["live_orders_permitted"] is False)
    ok(
        "shared_polygon_stated",
        "polygon" in (aiem_rep.get("shared_market_data") or {}),
    )

    from sku_isolation import (
        equity_book_sql_exclusion,
        is_sku_strategy_ticker,
        sku_strategy_ticker,
    )

    ok("ticker_aiem", sku_strategy_ticker("aiem", "SPY", "call_condor") == "AIEM:SPY:call_condor")
    ok("ticker_oe", sku_strategy_ticker("oe", "SPY", "call_condor") == "OE:SPY:call_condor")
    ok("is_sku_ticker", is_sku_strategy_ticker("OE:SPY:put_condor"))
    ok("not_equity_ticker", not is_sku_strategy_ticker("AAPL"))
    ok("equity_excl_has_prefixes", "AIEM:%" in equity_book_sql_exclusion() and "OE:%" in equity_book_sql_exclusion())

    print("FAIL_COUNT", fail)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
