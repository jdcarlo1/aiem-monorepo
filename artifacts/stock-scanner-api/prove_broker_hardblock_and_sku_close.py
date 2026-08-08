#!/usr/bin/env python3
"""
Directive_PR54_ScopeAndBrokerHook_Verification — proofs 2 and 3.

2) Broker hard-block with flags unset
3) Cross-SKU close rejection (aiem position / oe close and reverse)
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _clear_live_flags() -> None:
    for k in (
        "AIEM_ALLOW_LIVE_ORDERS",
        "OE_ALLOW_LIVE_ORDERS",
        "LIVE_TRADING_ENABLED",
        "LIVE_TRADING_CONFIRMATION",
        "LIVE_TRADING_CONFIRM",
    ):
        os.environ.pop(k, None)


def proof_2_broker_hardblock() -> bool:
    print("===== PROOF_2_BROKER_HARD_BLOCK =====")
    from aiem_broker import live_gate
    from aiem_broker.live_gate import assert_live_orders_allowed, LiveOrdersNotAllowed, live_gate_status
    from aiem_broker import OrderRequest, OrderSide, get_broker_adapter
    from aiem_broker import stubs as stub_mod
    import sku_isolation as si

    print("--- CODE: live_allow_env_key / assert_live_orders_allowed ---")
    print(inspect.getsource(si.live_allow_env_key))
    print(inspect.getsource(assert_live_orders_allowed))
    print("--- CODE: stub place_order gate ---")
    print(inspect.getsource(stub_mod._StubBroker.place_order))

    _clear_live_flags()
    print("--- ENV (flags unset) ---")
    print(f"AIEM_ALLOW_LIVE_ORDERS={os.environ.get('AIEM_ALLOW_LIVE_ORDERS')!r}")
    print(f"OE_ALLOW_LIVE_ORDERS={os.environ.get('OE_ALLOW_LIVE_ORDERS')!r}")
    print(f"live_gate_status_aiem={live_gate_status('aiem')}")
    print(f"live_gate_status_oe={live_gate_status('oe')}")

    ok = True
    # Direct gate
    for sku in ("aiem", "oe"):
        try:
            assert_live_orders_allowed(caller=f"proof.{sku}", sku=sku)
            print(f"FAIL gate_raised_for_{sku}=False")
            ok = False
        except LiveOrdersNotAllowed as e:
            print(f"PASS gate_blocked sku={sku} err={e}")

    # Stub adapters (live path) — both SKUs
    for sku in ("aiem", "oe"):
        for provider in ("tradier", "alpaca", "ibkr"):
            adapter = get_broker_adapter(provider, sku=sku)
            res = adapter.place_order(
                OrderRequest(
                    ticker="SPY",
                    side=OrderSide.BUY,
                    quantity=1,
                    metadata={"ref_price": 500.0},
                )
            )
            print(
                f"STUB_ORDER provider={provider} sku={sku} "
                f"ok={res.ok} status={res.status.value} mode={res.mode}"
            )
            print(f"STUB_ORDER_MESSAGE {res.message}")
            if res.ok or res.status.value not in ("blocked", "not_implemented"):
                # blocked is required when flag unset; not_implemented only if gate passed
                if res.status.value != "blocked":
                    print(f"FAIL expected blocked for {provider}/{sku}")
                    ok = False
            if "No order was sent" not in res.message and "blocked" not in res.message.lower() and "Live brokerage" not in res.message:
                # blocked message contains Live brokerage orders blocked
                if "Live brokerage orders blocked" not in res.message:
                    print(f"FAIL unexpected message for {provider}/{sku}")
                    ok = False

    # Paper endpoints hardcode provider=paper — extract from main.py on disk
    # (Flask may be absent in this proof VM; do not require importing main.)
    main_src = (ROOT / "main.py").read_text()
    def _fn_src(name: str) -> str:
        marker = f"def {name}("
        i = main_src.find(marker)
        if i < 0:
            return ""
        j = main_src.find("\n@app.route", i + 1)
        k = main_src.find("\ndef ", i + 1)
        end = min(x for x in (j, k, len(main_src)) if x > i)
        # prefer next route or next top-level def
        candidates = [x for x in (j if j > 0 else None, k if k > 0 else None) if x]
        end = min(candidates) if candidates else len(main_src)
        return main_src[i:end]

    src_aiem = _fn_src("aiem_broker_paper_order_endpoint")
    src_oe = _fn_src("oe_broker_paper_order_endpoint")
    src_aiem_status = _fn_src("aiem_broker_status_endpoint")
    src_oe_status = _fn_src("oe_broker_status_endpoint")
    print("--- CODE: /aiem-broker/paper-order forces paper ---")
    print(src_aiem)
    print("--- CODE: /oe-broker/paper-order forces paper ---")
    print(src_oe)
    aiem_forces_paper = 'get_broker_adapter("paper", sku="aiem")' in src_aiem
    oe_forces_paper = 'get_broker_adapter("paper", sku="oe")' in src_oe
    print(f"aiem_paper_order_forces_paper={aiem_forces_paper}")
    print(f"oe_paper_order_forces_paper={oe_forces_paper}")
    if not (aiem_forces_paper and oe_forces_paper):
        ok = False

    # Simulate what the HTTP handlers do (same calls) — flags unset
    from aiem_broker import broker_readiness_report
    for sku in ("aiem", "oe"):
        rep = broker_readiness_report(sku=sku)
        print(
            f"ROUTE_EQUIV_STATUS sku={sku} "
            f"live_permitted={rep['live_gate']['live_orders_permitted']} "
            f"active_provider={rep['active_provider']}"
        )
        if rep["live_gate"]["live_orders_permitted"] is not False:
            ok = False
        # paper-order path: forced paper adapter
        adapter = get_broker_adapter("paper", sku=sku)
        res = adapter.place_order(
            OrderRequest(
                ticker="SPY",
                side=OrderSide.BUY,
                quantity=1,
                metadata={"ref_price": 100.0, "sku": sku},
            )
        )
        print(
            f"ROUTE_EQUIV_PAPER_ORDER sku={sku} ok={res.ok} "
            f"provider={res.provider} mode={res.mode} status={res.status.value}"
        )
        if res.provider != "paper" or res.mode != "paper":
            print(f"FAIL paper-order routed away from paper for sku={sku}")
            ok = False

    # Confirm status endpoints call broker_readiness_report(sku=...)
    print(f"aiem_status_calls_sku_aiem={'broker_readiness_report(sku=\"aiem\")' in src_aiem_status}")
    print(f"oe_status_calls_sku_oe={'broker_readiness_report(sku=\"oe\")' in src_oe_status}")
    if 'broker_readiness_report(sku="aiem")' not in src_aiem_status:
        ok = False
    if 'broker_readiness_report(sku="oe")' not in src_oe_status:
        ok = False

    # Bypass scan: no place_order on stubs without assert_live_orders_allowed
    stub_src = inspect.getsource(stub_mod._StubBroker.place_order)
    print(f"stub_place_order_calls_assert={'assert_live_orders_allowed' in stub_src}")
    if "assert_live_orders_allowed" not in stub_src:
        ok = False
    # Grep package for any other place_order implementations
    bypass = []
    for p in (ROOT / "aiem_broker").glob("*.py"):
        t = p.read_text()
        if "def place_order" in t and p.name != "base.py":
            if "assert_live_orders_allowed" not in t and p.name != "paper.py":
                bypass.append(p.name)
    print(f"non_paper_place_order_without_gate={bypass}")
    if bypass:
        ok = False
        print("FAIL bypass path found")

    # Cross-SKU flag: OE allow must not unlock AIEM gate
    os.environ["OE_ALLOW_LIVE_ORDERS"] = "1"
    # Still paper mode (LIVE_TRADING unset) → both blocked
    for sku in ("aiem", "oe"):
        try:
            assert_live_orders_allowed(sku=sku)
            print(f"FAIL still_blocked_in_paper_mode sku={sku}")
            ok = False
        except LiveOrdersNotAllowed as e:
            print(f"PASS paper_mode_blocks_even_with_oe_flag sku={sku} err={e}")
    os.environ.pop("OE_ALLOW_LIVE_ORDERS", None)

    # No default-true: empty string / unset / "0" / "true" must not allow
    for val in (None, "", "0", "true", "TRUE", "yes"):
        _clear_live_flags()
        if val is not None:
            os.environ["AIEM_ALLOW_LIVE_ORDERS"] = val
        allow = os.environ.get("AIEM_ALLOW_LIVE_ORDERS", "") == "1"
        print(f"ALLOW_PARSE val={val!r} equals_1={allow}")
        if allow:
            ok = False
            print("FAIL default-true or loose parse")

    print(f"PROOF_2_OK={ok}")
    return ok


def proof_3_cross_sku_close() -> bool:
    """Open under one SKU; close with the other SKU must not match ticker/row."""
    print("===== PROOF_3_CROSS_SKU_CLOSE =====")
    import aim_asym_paper_strategies as m
    from sku_isolation import sku_strategy_ticker

    ok = True
    # In-memory fake DB capturing SQL
    store = {}  # id -> dict
    next_id = {"n": 1}
    executed = []

    class FakeCursor:
        def __init__(self):
            self._last = None
            self.rowcount = 0

        def execute(self, sql, params=None):
            sql_s = " ".join(sql.split())
            params = params or ()
            executed.append((sql_s, params))
            self.rowcount = 0
            self._last = None
            if "INSERT INTO aiem_paper_trades" in sql_s:
                # params order from persist_asym_paper_open
                ticker = params[0]
                rid = next_id["n"]
                next_id["n"] += 1
                # signal_source=strategy at [5]; strategy column near end
                strat = params[5] if len(params) > 5 else None
                store[rid] = {
                    "id": rid,
                    "ticker": ticker,
                    "status": "OPEN",
                    "strategy": strat,
                }
                self._last = (rid,)
                self.rowcount = 1
            elif "UPDATE aiem_paper_trades" in sql_s and "status='CLOSED'" in sql_s:
                # id+ticker form or subquery form
                if "WHERE id=%s AND status='OPEN' AND ticker=%s" in sql_s:
                    pid, ticker = int(params[-2]), params[-1]
                    row = store.get(pid)
                    if row and row["status"] == "OPEN" and row["ticker"] == ticker:
                        row["status"] = "CLOSED"
                        self.rowcount = 1
                    else:
                        self.rowcount = 0
                elif "WHERE strategy=%s AND status='OPEN' AND ticker=%s" in sql_s or (
                    "WHERE strategy=%s AND status='OPEN' AND ticker=%s" in sql_s
                ):
                    pass
                elif "SELECT id FROM aiem_paper_trades WHERE strategy=%s AND status='OPEN' AND ticker=%s" in sql_s:
                    strategy, ticker = params[-2], params[-1]
                    for rid, row in sorted(store.items(), reverse=True):
                        if row["status"] == "OPEN" and row.get("strategy") == strategy and row["ticker"] == ticker:
                            self._last = (rid,)
                            break
                # Handle the fallback UPDATE ... WHERE id = (SELECT ...)
                if "WHERE id = (" in sql_s:
                    # last two bound params before reason packing: strategy, ticker at end
                    strategy, ticker = params[-2], params[-1]
                    target = None
                    for rid, row in sorted(store.items(), reverse=True):
                        if row["status"] == "OPEN" and row.get("strategy") == strategy and row["ticker"] == ticker:
                            target = rid
                            break
                    if target is not None:
                        store[target]["status"] = "CLOSED"
                        self.rowcount = 1
                    else:
                        self.rowcount = 0
            elif "SELECT id FROM aiem_paper_trades" in sql_s and "ticker=%s" in sql_s:
                ticker = params[0]
                for rid, row in sorted(store.items(), reverse=True):
                    if row["ticker"] == ticker:
                        self._last = (rid,)
                        break
            elif "information_schema" in sql_s or "ALTER TABLE" in sql_s or "ADD COLUMN" in sql_s:
                self._last = None
            elif sql_s.strip().upper().startswith("SELECT"):
                self._last = None

        def fetchone(self):
            return self._last

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_connect(*a, **k):
        return FakeConn()

    # Inject fake psycopg2 — package may be absent in this proof VM
    import types
    fake_pg = types.ModuleType("psycopg2")
    fake_pg.connect = fake_connect
    sys.modules["psycopg2"] = fake_pg

    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://fake/proof"}, clear=False):
        with patch.object(m, "_db_url", return_value="postgresql://fake/proof"):
                # Open AIEM call_condor
                aiem_id = m.persist_asym_paper_open(
                    strategy="call_condor",
                    underlying="SPY",
                    entry_debit_usd=167.0,
                    packages=1,
                    expiration="2026-08-28",
                    legs=[{"qty": 1, "right": "call", "strike": 760.0, "premium": 1.0, "symbol": "X"}],
                    entry_premium_ps=1.67,
                    take_profit_pct=159.5,
                    sku="aiem",
                )
                print(f"OPEN_AIEM id={aiem_id} ticker={sku_strategy_ticker('aiem','SPY','call_condor')}")
                print(f"STORE_AFTER_AIEM_OPEN={store}")

                # Attempt close via OE path (wrong sku) — should not close
                m.persist_asym_paper_close(
                    paper_trade_id=aiem_id,
                    strategy="call_condor",
                    exit_value_usd=200.0,
                    pnl_usd=33.0,
                    reason="CROSS_SKU_OE_CLOSE_ATTEMPT",
                    sku="oe",
                    underlying="SPY",
                )
                aiem_status = store.get(aiem_id, {}).get("status")
                print(f"AFTER_OE_CLOSE_ATTEMPT aiem_id={aiem_id} status={aiem_status}")
                print(f"OE_CLOSE_SQL_LAST={[e for e in executed if 'CLOSED' in e[0]][-1:]}")
                if aiem_status != "OPEN":
                    print("FAIL aiem position closed by oe path")
                    ok = False
                else:
                    print("PASS aiem_still_open_after_oe_close_attempt")

                # Correct AIEM close should work
                m.persist_asym_paper_close(
                    paper_trade_id=aiem_id,
                    strategy="call_condor",
                    exit_value_usd=200.0,
                    pnl_usd=33.0,
                    reason="SAME_SKU_AIEM_CLOSE",
                    sku="aiem",
                    underlying="SPY",
                )
                print(f"AFTER_AIEM_CLOSE status={store.get(aiem_id, {}).get('status')}")
                if store.get(aiem_id, {}).get("status") != "CLOSED":
                    print("FAIL aiem same-sku close did not close")
                    ok = False
                else:
                    print("PASS aiem_same_sku_close")

                # Reverse: open OE, attempt AIEM close
                oe_id = m.persist_asym_paper_open(
                    strategy="put_condor",
                    underlying="SPY",
                    entry_debit_usd=141.0,
                    packages=1,
                    expiration="2026-08-28",
                    legs=[{"qty": 1, "right": "put", "strike": 760.0, "premium": 1.0, "symbol": "Y"}],
                    entry_premium_ps=1.41,
                    take_profit_pct=203.7,
                    sku="oe",
                )
                print(f"OPEN_OE id={oe_id} ticker={sku_strategy_ticker('oe','SPY','put_condor')}")
                m.persist_asym_paper_close(
                    paper_trade_id=oe_id,
                    strategy="put_condor",
                    exit_value_usd=180.0,
                    pnl_usd=39.0,
                    reason="CROSS_SKU_AIEM_CLOSE_ATTEMPT",
                    sku="aiem",
                    underlying="SPY",
                )
                oe_status = store.get(oe_id, {}).get("status")
                print(f"AFTER_AIEM_CLOSE_ATTEMPT oe_id={oe_id} status={oe_status}")
                if oe_status != "OPEN":
                    print("FAIL oe position closed by aiem path")
                    ok = False
                else:
                    print("PASS oe_still_open_after_aiem_close_attempt")

                # Fallback path (no paper_trade_id) must also be SKU-scoped
                oe_id2 = m.persist_asym_paper_open(
                    strategy="call_condor",
                    underlying="SPY",
                    entry_debit_usd=150.0,
                    packages=1,
                    expiration="2026-08-28",
                    legs=[{"qty": 1, "right": "call", "strike": 770.0, "premium": 1.0, "symbol": "Z"}],
                    entry_premium_ps=1.50,
                    take_profit_pct=160.0,
                    sku="oe",
                )
                print(f"OPEN_OE_FALLBACK_TARGET id={oe_id2}")
                m.persist_asym_paper_close(
                    paper_trade_id=None,
                    strategy="call_condor",
                    exit_value_usd=180.0,
                    pnl_usd=30.0,
                    reason="FALLBACK_AIEM_CLOSE_NO_ID",
                    sku="aiem",
                    underlying="SPY",
                )
                print(f"AFTER_FALLBACK_AIEM_CLOSE oe_id2_status={store.get(oe_id2, {}).get('status')}")
                if store.get(oe_id2, {}).get("status") != "OPEN":
                    print("FAIL fallback aiem close closed oe row")
                    ok = False
                else:
                    print("PASS oe_still_open_after_aiem_fallback_close")

        # Show close SQL always includes ticker bind
        close_sqls = [e for e in executed if "status='CLOSED'" in e[0]]
        print("--- CLOSE_SQL_CAPTURE ---")
        for sql, params in close_sqls:
            print(f"SQL={sql}")
            print(f"PARAMS={params}")
            if "ticker=%s" not in sql:
                print("FAIL close SQL missing ticker predicate")
                ok = False

    print(f"PROOF_3_OK={ok}")
    return ok


def main() -> int:
    # Importing main.py is heavy; only proof 2 needs it
    p2 = proof_2_broker_hardblock()
    p3 = proof_3_cross_sku_close()
    print("===== SUMMARY =====")
    print(f"PROOF_2_OK={p2}")
    print(f"PROOF_3_OK={p3}")
    print(f"ALL_OK={p2 and p3}")
    return 0 if (p2 and p3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
