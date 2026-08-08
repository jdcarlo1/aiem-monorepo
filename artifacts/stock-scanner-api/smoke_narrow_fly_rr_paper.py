#!/usr/bin/env python3
"""Smoke: narrow-wing fly + bullish RR paper ledgers (no Polygon needed)."""
from __future__ import annotations

import sys

from aim_asym_paper_strategies import (
    STRATEGY_KEYS,
    AsymOptionsLedger,
    build_bullish_risk_reversal,
    build_default_asym_ledgers,
    build_narrow_wing_call_butterfly,
    _short_put_collateral_usd,
)


def main() -> int:
    ledgers = build_default_asym_ledgers()
    assert set(STRATEGY_KEYS) == set(ledgers.keys()), (
        STRATEGY_KEYS, list(ledgers.keys())
    )
    for k in STRATEGY_KEYS:
        snap = ledgers[k].snapshot()
        assert snap["strategy"] == k
        assert snap["rules"]["stop_loss"] is None

    nw = ledgers["narrow_wing_butterfly"]
    assert nw.take_profit_pct == 300.0
    assert nw.allow_credit is False
    assert build_narrow_wing_call_butterfly(500.0) == [
        (1, "call", 498.0), (-2, "call", 500.0), (1, "call", 502.0)
    ]

    rr = ledgers["bullish_risk_reversal"]
    assert rr.take_profit_pct == 75.0
    assert rr.allow_credit is True
    assert rr.cash_secured is True
    assert rr._starting_capital >= 50_000.0
    assert build_bullish_risk_reversal(500.0) == [
        (1, "call", 505.0), (-1, "put", 495.0)
    ]

    # TP math: credit entry −200, mark −20 → pnl 180; TP@75% needs ≥150
    rr.active_position = {
        "entry_debit_usd": -200.0,
        "packages": 1,
        "legs": [],
        "expiration": "2099-01-01",
        "direction": "BULLISH_RISK_REVERSAL",
        "collateral_usd": 49500.0,
    }
    rr._reserved_collateral_usd = 49500.0
    rr.account_balance_usd = 100_000.0 + 200.0  # after receiving credit
    entry = -200.0
    mark = -20.0
    pnl = mark - entry
    tp = abs(entry) * (rr.take_profit_pct / 100.0)
    assert pnl >= tp, (pnl, tp)
    rr._close(mark, "TP_75PCT")
    assert rr.active_position is None
    assert rr.wins == 1
    assert abs(rr._reserved_collateral_usd) < 1e-9
    assert abs(rr.account_balance_usd - (100200.0 - 20.0)) < 1e-6

    coll = _short_put_collateral_usd(
        [{"qty": -1, "right": "put", "strike": 495.0}], 1
    )
    assert abs(coll - 49500.0) < 1e-6

    # Debit TP still uses abs(entry) * pct (same as old mark >= entry*(1+pct/100))
    fly = AsymOptionsLedger(
        "TEST_FLY", build_narrow_wing_call_butterfly, 200.0, "narrow_wing_butterfly"
    )
    fly.account_balance_usd = 9800.0
    fly.active_position = {
        "entry_debit_usd": 200.0,
        "packages": 1,
        "legs": [],
        "expiration": "2099-01-01",
        "direction": "NARROW_WING_CALL_BUTTERFLY",
        "collateral_usd": 0.0,
    }
    # pnl at mark 600 = 400; TP needs 400 → fire
    assert (600.0 - 200.0) >= abs(200.0) * 2.0
    fly._close(600.0, "TP_200PCT")
    assert fly.wins == 1

    print("OK smoke_narrow_fly_rr_paper")
    return 0


if __name__ == "__main__":
    sys.exit(main())
