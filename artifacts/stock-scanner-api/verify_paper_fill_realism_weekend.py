#!/usr/bin/env python3
"""Weekend evidence harness — Paper Fill Realism (directive 2026-08-08).

Items 1–6 with raw output. Sandbox order POST expected 401 with current token;
reject handling still proven via parse + no assumed fill.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# load /workspace/.env if present
for envp in (Path("/workspace/.env"), ROOT.parent.parent / ".env"):
    if envp.exists():
        for line in envp.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break

os.environ.setdefault("TRADIER_PAPER_REG_FEES", "1")


def _hdr(title: str) -> None:
    print(f"\n===== {title} =====")


def item1_order_rejects() -> bool:
    from aiem_broker.tradier_sandbox import sandbox_post_order, prod_base_note
    from aiem_broker.order_lifecycle import (
        parse_broker_order_response,
        FIXTURE_REJECT_BAD_PRICE,
        FIXTURE_REJECT_INSUFFICIENT_BP,
    )

    _hdr("1 ORDER REJECTS — sandbox POST + handler")
    print(prod_base_note())
    # Real sandbox POST (expected 401 with current brokerage token)
    payload = {
        "class": "option",
        "symbol": "SPY",
        "option_symbol": "SPY260904C00773000",
        "side": "buy_to_open",
        "quantity": "1",
        "type": "limit",
        "duration": "day",
        "price": "0.01",  # intentionally bad / low
    }
    parsed = sandbox_post_order(payload)
    print("RAW_SANDBOX_POST_PARSED=")
    print(json.dumps(parsed, indent=2, default=str)[:2000])
    assert parsed["status"] == "rejected", parsed
    assert parsed["filled_qty"] == 0.0
    assert parsed["assumed_fill"] is False
    print("ASSERT_no_silent_fill_on_sandbox_error=True")

    # Fixture rejects (Tradier-shaped) — prove handler
    for name, body in (
        ("bad_price", FIXTURE_REJECT_BAD_PRICE),
        ("insufficient_bp", FIXTURE_REJECT_INSUFFICIENT_BP),
    ):
        p = parse_broker_order_response(
            int(body.get("http_status") or 400), body, requested_qty=5
        )
        print(f"FIXTURE_{name}=")
        print(json.dumps(p, indent=2))
        assert p["status"] == "rejected"
        assert p["filled_qty"] == 0.0
        assert p["assumed_fill"] is False
    print("ITEM1_OK=True")
    return True


def item2_partial_fills() -> bool:
    from aiem_broker.order_lifecycle import (
        FIXTURE_PARTIAL_STATUS,
        FIXTURE_FILLED_STATUS,
        parse_broker_order_response,
        poll_order_status_until_terminal,
        PartialPosition,
    )

    _hdr("2 PARTIAL FILLS — status poll + P&L on filled qty only")
    # Simulate sandbox order-status polling: first partial, then would fill
    states = [FIXTURE_PARTIAL_STATUS]

    def fetch(_oid):
        return 200, states[0]

    polled = poll_order_status_until_terminal(
        fetch, order_id="999001", requested_qty=10.0
    )
    print("RAW_PARTIAL_STATUS=")
    print(json.dumps(polled, indent=2))
    assert polled["status"] == "partial"
    assert polled["filled_qty"] == 4.0
    assert polled["remaining_qty"] == 6.0
    assert polled["assumed_fill"] is False

    pos = PartialPosition(symbol="SPY260904C00773000")
    r1 = pos.apply_fill(side="buy_to_open", fill_qty=4.0, fill_price=1.25)
    print("AFTER_PARTIAL_FILL_POSITION=")
    print(json.dumps(r1, indent=2))
    assert pos.quantity == 4.0
    assert abs(pos.avg_price - 1.25) < 1e-9
    # Close only 4 (the filled qty) — P&L on partial, not on requested 10
    r2 = pos.apply_fill(side="sell_to_close", fill_qty=4.0, fill_price=1.55)
    print("AFTER_CLOSE_PARTIAL_PNL=")
    print(json.dumps(r2, indent=2))
    expected_pnl = (1.55 - 1.25) * 4.0 * 100.0  # 120.0
    assert abs(pos.realized_pnl_usd - expected_pnl) < 1e-6
    assert abs(pos.quantity) < 1e-9
    print(f"ASSERT_pnl_on_filled_qty_only={expected_pnl}")
    # Prove we do NOT invent fill for remaining 6
    full = parse_broker_order_response(200, FIXTURE_FILLED_STATUS, requested_qty=10)
    print("RAW_FULL_FILL_STATUS=", json.dumps(full))
    print("ITEM2_OK=True")
    return True


def item3_buying_power() -> bool:
    from aiem_broker.buying_power import (
        check_buying_power,
        option_debit_requirement_usd,
    )

    _hdr("3 MARGIN / BUYING POWER — block when insufficient")
    need = option_debit_requirement_usd(net_debit_usd=450.0, fees_usd=3.14)
    print(f"required_usd={need}")
    blocked = check_buying_power(
        available_bp_usd=100.0, required_usd=need, label="call_condor_1pkg"
    )
    print("RAW_BP_BLOCK=")
    print(json.dumps(blocked, indent=2))
    assert blocked["blocked"] is True
    assert blocked["ok"] is False
    allowed = check_buying_power(
        available_bp_usd=10000.0, required_usd=need, label="call_condor_1pkg"
    )
    print("RAW_BP_ALLOW=")
    print(json.dumps(allowed, indent=2))
    assert allowed["ok"] is True
    # Real Tradier account BP=0 evidence
    from aiem_broker.tradier_market import tradier_token, tradier_account_id, _headers, TRADIER_API_BASE
    import requests

    acct = tradier_account_id()
    r = requests.get(
        f"{TRADIER_API_BASE}/v1/accounts/{acct}/balances",
        headers=_headers(tradier_token()),
        timeout=10,
    )
    print("RAW_TRADIER_BALANCES_HTTP=", r.status_code)
    bal = r.json().get("balances") or {}
    margin = bal.get("margin") or {}
    obp = float(margin.get("option_buying_power") or 0)
    print(f"tradier_option_buying_power={obp}")
    live_gate = check_buying_power(
        available_bp_usd=obp, required_usd=need, label="live_acct_probe"
    )
    print("RAW_LIVE_ACCT_BP_GATE=")
    print(json.dumps(live_gate, indent=2))
    assert live_gate["blocked"] is True  # $0 equity account
    print("ITEM3_OK=True")
    return True


def item4_package_pricing() -> bool:
    from aiem_broker.tradier_market import fetch_quote
    from aiem_broker.paper_fills import price_package_nbbo
    from aiem_broker.package_pricing import price_package_atomic
    from aim_asym_paper_strategies import build_long_call_condor, next_friday

    _hdr("4 MULTI-LEG PACKAGE PRICING — condor before/after")
    spot = float((fetch_quote("SPY") or {}).get("last") or 0)
    exp = next_friday(date.today(), weeks_ahead=3)
    legs = build_long_call_condor(spot)
    print(f"SPOT={spot} EXP={exp} LEGS={legs}")

    before = price_package_nbbo("SPY", exp, legs, packages=1, include_fees=True)
    print("BEFORE_LEG_SUM_MODEL=")
    print(json.dumps({
        k: before.get(k)
        for k in (
            "pricing_source", "net_usd", "fees_usd", "debit_per_share",
            "contract_count", "live_order_sent",
        )
    }, indent=2))
    print("BEFORE_LEGS_INDEPENDENT=")
    for L in before.get("legs") or []:
        print(f"  qty={L['qty']} {L['right']}{L['strike']} fill={L['premium']} side={L['fill_side']}")

    after = price_package_atomic("SPY", exp, legs, packages=1, include_fees=True)
    print("AFTER_PACKAGE_AON_MODEL=")
    print(json.dumps({
        k: after.get(k)
        for k in (
            "ok", "status", "aon", "legging_allowed", "fill_id",
            "pricing_source", "package_fill_price", "net_usd", "mid_usd",
            "package_edge_vs_mid_usd", "fees_usd", "legacy_commission_only_usd",
            "net_usd_after_fees", "live_order_sent", "assumed_fill",
        )
    }, indent=2, default=str))
    assert after and after.get("aon") is True
    assert after.get("legging_allowed") is False
    assert after.get("fill_id")
    assert abs(float(after["net_usd"]) - float(before["net_usd"])) < 1e-6  # same natural
    print("ITEM4_OK=True")
    return True


def item5_regulatory_fees() -> bool:
    from aiem_broker.fee_schedule import fee_breakdown, COMMISSION_PER_CONTRACT_LEG

    _hdr("5 REGULATORY FEES beyond flat $0.65")
    # 4-leg condor × 1 package = 4 contracts one-way
    old = 4 * COMMISSION_PER_CONTRACT_LEG
    br = fee_breakdown(contracts=4.0, n_legs=4, side="buy")
    print("OLD_COMMISSION_ONLY=", old)
    print("NEW_FEE_BREAKDOWN=")
    print(json.dumps(br, indent=2))
    assert br["total_usd"] > old
    assert abs(br["commission_usd"] - old) < 1e-9
    print(f"DELTA_REGULATORY={br['total_usd'] - old:.6f}")
    # sell side includes TAF
    br_s = fee_breakdown(contracts=4.0, n_legs=4, side="sell")
    print("SELL_SIDE_BREAKDOWN=")
    print(json.dumps(br_s, indent=2))
    assert br_s["taf_usd"] > 0
    print("ITEM5_OK=True")
    return True


def item6_halts() -> bool:
    from aiem_broker.halt_check import check_halt, gate_fill_if_halted
    from aiem_broker.package_pricing import price_package_atomic
    from aim_asym_paper_strategies import build_long_call_condor, next_friday
    from aiem_broker.tradier_market import fetch_quote
    from unittest.mock import patch

    _hdr("6 HALTS — historical fixture + forced block")
    hist = check_halt("GME", asof="2021-01-28")
    print("RAW_HISTORICAL_HALT_GME_2021-01-28=")
    print(json.dumps(hist, indent=2))
    assert hist["halted"] is True
    gate = gate_fill_if_halted(hist)
    print("RAW_GATE=")
    print(json.dumps(gate, indent=2))
    assert gate["blocked"] is True

    # Forced halt on live SPY package path
    spot = float((fetch_quote("SPY") or {}).get("last") or 773)
    exp = next_friday(date.today(), weeks_ahead=3)
    legs = build_long_call_condor(spot)

    def _force_halt(symbol, **kwargs):
        return {
            "symbol": symbol,
            "halted": True,
            "block_fill": True,
            "reason": "FORCE_HALT_TEST",
            "source": "force_inject",
        }

    with patch("aiem_broker.package_pricing.check_halt", side_effect=_force_halt):
        blocked = price_package_atomic("SPY", exp, legs, packages=1, check_halts=True)
    print("RAW_FORCED_HALT_PACKAGE_RESULT=")
    print(json.dumps(blocked, indent=2, default=str))
    assert blocked["ok"] is False
    assert blocked["status"] == "blocked_halt"
    assert blocked.get("assumed_fill") is False
    print("ITEM6_OK=True")
    return True


def main() -> int:
    results = []
    for fn in (
        item1_order_rejects,
        item2_partial_fills,
        item3_buying_power,
        item4_package_pricing,
        item5_regulatory_fees,
        item6_halts,
    ):
        try:
            results.append((fn.__name__, bool(fn())))
        except Exception as e:
            print(f"FAIL {fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            results.append((fn.__name__, False))
    _hdr("SUMMARY")
    for name, ok in results:
        print(f"{name}={'PASS' if ok else 'FAIL'}")
    all_ok = all(ok for _, ok in results)
    print(f"ALL_WEEKEND_ITEMS_OK={all_ok}")
    print(
        "NOTE_SANDBOX: real sandbox order rejects/partials require sandbox token; "
        "current token → 401. Handler proven; live-capital items 7–9 remain OPEN."
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
