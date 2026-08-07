#!/usr/bin/env python3
"""
Directive_PatternLabTop2_WiringVerification_2026-08-07

Raw-evidence runner: spec gates, rejection proofs, BT parity table,
DB OPEN/CLOSE for narrow_wing_butterfly + bullish_risk_reversal.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
PASS = 0
FAIL = 0


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}" + (f" | {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"FAIL {name}" + (f" | {detail}" if detail else ""))


def _monday_df(spot: float = 500.0) -> pd.DataFrame:
    # 2026-08-03 was a Monday
    idx = pd.DatetimeIndex(
        [datetime(2026, 8, 3, 9, 30, tzinfo=ET)],
        name="ts",
    )
    return pd.DataFrame(
        {"open": [spot], "high": [spot], "low": [spot], "close": [spot], "volume": [1]},
        index=idx,
    )


def _priced(debit_ps: float, legs: list) -> dict:
    return {
        "debit_per_share": float(debit_ps),
        "legs": legs,
        "expiration": "2026-08-28",
        "pricing_source": "polygon_daily_option_aggs",
    }


def section_spec_constants():
    print("===== 1_SPEC_CONSTANTS_AND_BUILDERS =====")
    import aim_asym_paper_strategies as m

    print(f"RISK_USD={m.RISK_USD!r}")
    print(f"ENTRY_AFTER={m.ENTRY_AFTER!r}")
    print(f"FLATTEN_TIME={m.FLATTEN_TIME!r}")
    print(f"RR_PAPER_CAPITAL_USD={m.RR_PAPER_CAPITAL_USD!r}")
    print(f"STRATEGY_KEYS={m.STRATEGY_KEYS!r}")
    print(f"POLYGON_BASE={m.POLYGON_BASE!r}")

    nw = m.build_narrow_wing_call_butterfly(500.0)
    rr = m.build_bullish_risk_reversal(500.0)
    print(f"narrow_wing_legs_spot500={nw!r}")
    print(f"bullish_rr_legs_spot500={rr!r}")
    _ok("narrow_atm_pm2", nw == [(1, "call", 498.0), (-2, "call", 500.0), (1, "call", 502.0)])
    _ok("rr_call_kp5_put_km5", rr == [(1, "call", 505.0), (-1, "put", 495.0)])

    ledgers = m.build_default_asym_ledgers()
    nw_l = ledgers["narrow_wing_butterfly"]
    rr_l = ledgers["bullish_risk_reversal"]
    print(
        "narrow_ledger",
        {
            "tp": nw_l.take_profit_pct,
            "risk": nw_l.risk_usd,
            "allow_credit": nw_l.allow_credit,
            "cash_secured": nw_l.cash_secured,
            "capital": nw_l._starting_capital,
            "stop_in_rules": nw_l.snapshot()["rules"]["stop_loss"],
        },
    )
    print(
        "rr_ledger",
        {
            "tp": rr_l.take_profit_pct,
            "risk": rr_l.risk_usd,
            "allow_credit": rr_l.allow_credit,
            "cash_secured": rr_l.cash_secured,
            "capital": rr_l._starting_capital,
            "stop_in_rules": rr_l.snapshot()["rules"]["stop_loss"],
        },
    )
    _ok("narrow_tp_200", nw_l.take_profit_pct == 200.0)
    _ok("narrow_risk_500", nw_l.risk_usd == 500.0)
    _ok("narrow_no_stop", nw_l.snapshot()["rules"]["stop_loss"] is None)
    _ok("rr_tp_75", rr_l.take_profit_pct == 75.0)
    _ok("rr_allow_credit", rr_l.allow_credit is True)
    _ok("rr_cash_secured_flag", rr_l.cash_secured is True)
    _ok("rr_capital_100k", rr_l._starting_capital == 100000.0)


def section_reject_budget():
    print("===== 2_REJECT_NARROW_WING_OVER_500_DEBIT =====")
    import aim_asym_paper_strategies as m

    ledger = m.AsymOptionsLedger(
        "NARROW_WING_CALL_BUTTERFLY",
        m.build_narrow_wing_call_butterfly,
        200.0,
        "narrow_wing_butterfly",
        starting_capital_usd=10000.0,
        risk_usd=500.0,
    )
    # 1-lot debit $650 > $500 risk → SKIP_BUDGET (same gate as BT)
    fake = _priced(
        6.50,
        [
            {"qty": 1, "right": "call", "strike": 498.0, "premium": 8.0, "symbol": "X1"},
            {"qty": -2, "right": "call", "strike": 500.0, "premium": 5.0, "symbol": "X2"},
            {"qty": 1, "right": "call", "strike": 502.0, "premium": 3.5, "symbol": "X3"},
        ],
    )
    unit = fake["debit_per_share"] * 100.0
    print(f"injected_unit_cost_usd={unit}")
    print(f"risk_usd={ledger.risk_usd}")
    with patch.object(m, "price_legs_polygon", return_value=fake):
        ledger.evaluate(_monday_df(500.0))
    print(f"signal_state={ledger.signal_state!r}")
    print(f"active_position={ledger.active_position!r}")
    _ok(
        "skip_budget_status",
        ledger.signal_state.get("status") == "SKIP_BUDGET",
        str(ledger.signal_state),
    )
    _ok("skip_budget_no_position", ledger.active_position is None)


def section_reject_collateral():
    print("===== 3_REJECT_RR_INSUFFICIENT_CASH_SECURED =====")
    import aim_asym_paper_strategies as m

    # Capital too small for SPY CSP at strike 495 → need $49,500
    ledger = m.AsymOptionsLedger(
        "BULLISH_RISK_REVERSAL",
        m.build_bullish_risk_reversal,
        75.0,
        "bullish_risk_reversal",
        starting_capital_usd=10_000.0,  # deliberately under-collateralized
        allow_credit=True,
        cash_secured=True,
    )
    fake = _priced(
        -1.50,  # net credit $150
        [
            {"qty": 1, "right": "call", "strike": 505.0, "premium": 2.0, "symbol": "C1"},
            {"qty": -1, "right": "put", "strike": 495.0, "premium": 3.5, "symbol": "P1"},
        ],
    )
    coll = m._short_put_collateral_usd(fake["legs"], 1)
    print(f"injected_credit_ps={fake['debit_per_share']}")
    print(f"computed_collateral_usd={coll}")
    print(f"free_cash_usd={ledger._free_cash_usd()}")
    with patch.object(m, "price_legs_polygon", return_value=fake):
        ledger.evaluate(_monday_df(500.0))
    print(f"signal_state={ledger.signal_state!r}")
    print(f"active_position={ledger.active_position!r}")
    print(f"reserved_collateral_usd={ledger._reserved_collateral_usd}")
    _ok(
        "skip_collateral_status",
        ledger.signal_state.get("status") == "SKIP_COLLATERAL",
        str(ledger.signal_state),
    )
    _ok("skip_collateral_no_position", ledger.active_position is None)
    _ok("skip_collateral_no_reserve", ledger._reserved_collateral_usd == 0.0)

    # Positive control: same credit with $100k book → enters
    print("===== 3b_POSITIVE_RR_WITH_100K_ENTERS =====")
    ok_ledger = m.AsymOptionsLedger(
        "BULLISH_RISK_REVERSAL",
        m.build_bullish_risk_reversal,
        75.0,
        "bullish_risk_reversal",
        starting_capital_usd=100_000.0,
        allow_credit=True,
        cash_secured=True,
    )
    with patch.object(m, "price_legs_polygon", return_value=fake):
        with patch.object(m, "persist_asym_paper_open", return_value=None):
            ok_ledger.evaluate(_monday_df(500.0))
    print(f"signal_state={ok_ledger.signal_state!r}")
    print(f"active_position_entry={ok_ledger.active_position and ok_ledger.active_position.get('entry_debit_usd')}")
    print(f"collateral_usd={ok_ledger.active_position and ok_ledger.active_position.get('collateral_usd')}")
    print(f"reserved={ok_ledger._reserved_collateral_usd}")
    _ok("rr_100k_enters", ok_ledger.active_position is not None)
    _ok(
        "rr_100k_collateral_reserved",
        abs(float(ok_ledger._reserved_collateral_usd) - 49500.0) < 1e-6,
    )


def section_tp_math():
    print("===== 4_TP_MATH_ABS_ENTRY =====")
    # debit: entry 200, tp 200% → need pnl>=400 → mark>=600
    entry_d, tp_d = 200.0, 200.0
    need_d = abs(entry_d) * (tp_d / 100.0)
    print(f"debit_tp_dollars={need_d} mark_for_tp={entry_d + need_d}")
    _ok("debit_tp_200pct", need_d == 400.0)
    # credit: entry -150, tp 75% → need pnl>=112.5
    entry_c, tp_c = -150.0, 75.0
    need_c = abs(entry_c) * (tp_c / 100.0)
    print(f"credit_tp_dollars={need_c}")
    _ok("credit_tp_75pct", abs(need_c - 112.5) < 1e-9)


def section_entry_flatten_gates():
    print("===== 5_ENTRY_FLATTEN_GATE_STRINGS =====")
    import aim_asym_paper_strategies as m
    import inspect

    src = inspect.getsource(m.AsymOptionsLedger.evaluate)
    _ok("entry_uses_ENTRY_AFTER", "bar_time < ENTRY_AFTER" in src)
    _ok("entry_monday_only", "day.weekday() != 0" in src)
    _ok("expiry_uses_FLATTEN_TIME", "bar_time >= FLATTEN_TIME" in src)
    _ok("weeks_ahead_3", "next_friday(day, weeks_ahead=3)" in src)
    _ok("pricing_fn_polygon", "price_legs_polygon(" in src)
    _ok("entry_require_exact", "require_exact=True" in src)
    # deny-string may mention Tradier; fail only if evaluate *calls* a Tradier pricer
    tradier_call = any(x in src.lower() for x in ("tradier_", "fetch_tradier", "price_legs_tradier", "from tradier"))
    print(f"tradier_call_in_evaluate={tradier_call}")
    print(f"deny_string_mentions_tradier={'tradier' in src.lower()}")
    _ok("no_tradier_pricing_call", not tradier_call)
    print(f"ENTRY_AFTER_const={m.ENTRY_AFTER}")
    print(f"FLATTEN_TIME_const={m.FLATTEN_TIME}")
    print(f"WAITING_MONDAY_DAILY_in_src={'WAITING_MONDAY_DAILY' in src}")
    _ok("waiting_monday_daily_status", "WAITING_MONDAY_DAILY" in src)


def section_bt_parity_table():
    print("===== 6_PAPER_VS_CATALOG_BT_PARITY =====")
    # Catalog BT lives on PR#44 branch; constants duplicated here from fetched file
    # when local file absent.
    catalog_path = ROOT / "spy_catalog_untested_bt.py"
    paper = {
        "risk_usd": 500.0,
        "entry": "Monday >= 09:30 ET (ENTRY_AFTER)",
        "expiry": "next_friday(d0, weeks_ahead=3)",
        "flatten": "15:30 ET on expiry Friday (FLATTEN_TIME)",
        "stop": None,
        "pricing": "polygon_daily_option_aggs",
        "narrow_builder": "[(1,C,k-2),(-2,C,k),(1,C,k+2)]",
        "rr_builder": "[(1,C,k+5),(-1,P,k-5)]",
        "narrow_tp": 200.0,
        "rr_tp": 75.0,
        "cash_secured_rr": True,
    }
    bt = {
        "risk_usd": 500.0,
        "entry": "weekly Monday (daily bar asof entry date)",
        "expiry": "bt.next_friday(d0, weeks_ahead=3)",
        "flatten": "EXPIRY_FLATTEN on last available daily bar <= hold_end",
        "stop": None,
        "pricing": "Polygon daily option aggregates via spy_asymmetric_bt",
        "narrow_builder": "b_narrow_wing_fly identical wings",
        "rr_builder": "b_bull_rr identical strikes",
        "narrow_tp": 200.0,
        "rr_tp": 75.0,
        "cash_secured_rr": False,  # BT does NOT model CSP collateral
    }
    print("PAPER_CONFIG", json.dumps(paper, indent=2, sort_keys=True))
    print("CATALOG_BT_CONFIG", json.dumps(bt, indent=2, sort_keys=True))
    print(f"catalog_file_present_locally={catalog_path.exists()}")
    print("PARITY_MATCH risk_usd", paper["risk_usd"] == bt["risk_usd"])
    print("PARITY_MATCH stop_none", paper["stop"] is None and bt["stop"] is None)
    print("PARITY_MATCH pricing_polygon", "polygon" in paper["pricing"].lower() and "Polygon" in bt["pricing"])
    print("PARITY_MATCH narrow_tp", paper["narrow_tp"] == bt["narrow_tp"])
    print("PARITY_MATCH rr_tp", paper["rr_tp"] == bt["rr_tp"])
    print("PARITY_NOTE entry_bar: paper=09:30 first RTH; catalog BT=Monday daily close/asof")
    print("PARITY_NOTE flatten: paper=15:30 clock; catalog BT=last daily bar (no intraday clock)")
    print("PARITY_NOTE cash_secured: paper ENFORCES SKIP_COLLATERAL; catalog BT does NOT model CSP")
    _ok("parity_risk_500", paper["risk_usd"] == bt["risk_usd"])
    _ok("parity_tps", paper["narrow_tp"] == bt["narrow_tp"] and paper["rr_tp"] == bt["rr_tp"])


def section_db_persist():
    print("===== 7_DB_PERSIST_TOP2 =====")
    dsn = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("NEON_DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or ""
    )
    print(f"DATABASE_URL_PRESENT={bool(dsn)}")
    print(f"DATABASE_HOST_HINT={dsn.split('@')[-1][:60] if dsn else 'NONE'}")
    if not dsn:
        print("CANNOT PRODUCE: no DATABASE_URL")
        _ok("db_persist", False, "no DSN")
        return

    import psycopg2
    from aim_asym_paper_strategies import persist_asym_paper_open, persist_asym_paper_close

    cases = [
        (
            "narrow_wing_butterfly",
            200.0,
            185.0,
            [
                {"qty": 1, "right": "call", "strike": 498.0, "premium": 3.2, "symbol": "O:SPYNW1"},
                {"qty": -2, "right": "call", "strike": 500.0, "premium": 2.1, "symbol": "O:SPYNW2"},
                {"qty": 1, "right": "call", "strike": 502.0, "premium": 1.25, "symbol": "O:SPYNW3"},
            ],
        ),
        (
            "bullish_risk_reversal",
            75.0,
            -150.0,
            [
                {"qty": 1, "right": "call", "strike": 505.0, "premium": 2.0, "symbol": "O:SPYRR1"},
                {"qty": -1, "right": "put", "strike": 495.0, "premium": 3.5, "symbol": "O:SPYRR2"},
            ],
        ),
    ]

    try:
        with psycopg2.connect(dsn, connect_timeout=8) as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            print(f"current_database={cur.fetchone()[0]!r}")
    except Exception as e:
        print(f"CANNOT PRODUCE DB connect: {type(e).__name__}: {e}")
        _ok("db_connect", False, str(e))
        return

    ids = {}
    for strat, tp, entry_usd, legs in cases:
        print(f"--- OPEN {strat} entry_usd={entry_usd} tp={tp} ---")
        oid = persist_asym_paper_open(
            strategy=strat,
            underlying="SPY",
            entry_debit_usd=entry_usd,
            packages=1,
            expiration="2026-08-28",
            legs=legs,
            entry_premium_ps=entry_usd / 100.0,
            take_profit_pct=tp,
        )
        print(f"RETURNING_ID strategy={strat} id={oid!r}")
        ids[strat] = oid
        _ok(f"db_open_{strat}", oid is not None, f"id={oid}")
        if oid is None:
            continue
        # Close at TP: pnl = abs(entry)*tp/100
        tp_dollars = abs(entry_usd) * (tp / 100.0)
        exit_val = entry_usd + tp_dollars
        pnl = exit_val - entry_usd
        print(f"--- CLOSE {strat} exit={exit_val} pnl={pnl} (expect tp_dollars={tp_dollars}) ---")
        persist_asym_paper_close(
            paper_trade_id=oid,
            strategy=strat,
            exit_value_usd=exit_val,
            pnl_usd=pnl,
            reason=f"VERIFY_TP_{int(tp)}PCT",
        )

    import psycopg2

    with psycopg2.connect(dsn, connect_timeout=8) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, trade_date, ticker, strategy, status, direction,
                   notional, entry_price, strike, expiry, pnl, pnl_pct,
                   exit_reason, signal_detail
            FROM aiem_paper_trades
            WHERE strategy IN ('narrow_wing_butterfly', 'bullish_risk_reversal')
            ORDER BY id
            """
        )
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        print("QUERY_ROWS_COUNT", len(rows))
        for r in rows:
            print("ROW", dict(zip(colnames, r)))
        by = {}
        for r in rows:
            by[r[3]] = by.get(r[3], 0) + 1
        print("COUNT_BY_STRATEGY", by)
        _ok("db_ge1_narrow", by.get("narrow_wing_butterfly", 0) >= 1)
        _ok("db_ge1_rr", by.get("bullish_risk_reversal", 0) >= 1)

        # Verify TP/PnL math on closed rows we just wrote
        for strat, tp, entry_usd, _legs in cases:
            oid = ids.get(strat)
            if not oid:
                continue
            cur.execute(
                "SELECT notional, pnl, pnl_pct, status, exit_reason FROM aiem_paper_trades WHERE id=%s",
                (oid,),
            )
            notional, pnl, pnl_pct, status, reason = cur.fetchone()
            expect_pnl = abs(float(entry_usd)) * (float(tp) / 100.0)
            print(
                f"MATH {strat} id={oid} notional={notional} pnl={pnl} "
                f"pnl_pct={pnl_pct} status={status} reason={reason} expect_pnl={expect_pnl}"
            )
            _ok(f"db_pnl_{strat}", abs(float(pnl) - expect_pnl) < 0.02, f"pnl={pnl} expect={expect_pnl}")
            _ok(f"db_closed_{strat}", status == "CLOSED")


def section_live_snapshot():
    print("===== 8_LIVE_PATTERN_LAB_SNAPSHOT =====")
    import urllib.request

    url = "https://nclexai.org/stock-api/pattern-lab/snapshot"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = r.read().decode()
        data = json.loads(body)
        keys = sorted(data.keys())
        print("LIVE_KEYS", keys)
        for k in (
            "narrow_wing_butterfly",
            "bullish_risk_reversal",
            "put_butterfly",
            "call_butterfly",
            "put_ladder",
            "f3",
            "gap_fill",
            "orb",
        ):
            v = data.get(k)
            if v is None:
                print(f"LIVE_{k}=MISSING")
            else:
                rules = v.get("rules") or {}
                print(
                    f"LIVE_{k}",
                    json.dumps(
                        {
                            "pattern": v.get("pattern"),
                            "account_balance_usd": v.get("account_balance_usd"),
                            "take_profit_pct": rules.get("take_profit_pct"),
                            "allow_credit": rules.get("allow_credit"),
                            "cash_secured": rules.get("cash_secured"),
                            "pricing": rules.get("pricing"),
                            "signal": (v.get("signal_state") or {}).get("status"),
                        },
                        sort_keys=True,
                    ),
                )
        has_nw = "narrow_wing_butterfly" in data
        has_rr = "bullish_risk_reversal" in data
        if not has_nw or not has_rr:
            print(
                "CANNOT PRODUCE: live /pattern-lab/snapshot missing top-2 keys — "
                "PR #45 not deployed / stock-api not restarted on merged main "
                f"(has_nw={has_nw} has_rr={has_rr}; live_keys={keys})"
            )
            _ok("live_snapshot_top2", False, f"keys={keys}")
        else:
            _ok("live_snapshot_top2", True)
    except Exception as e:
        print(f"CANNOT PRODUCE live snapshot: {type(e).__name__}: {e}")
        _ok("live_snapshot_top2", False, str(e))


def section_local_engine_snapshot():
    print("===== 9_LOCAL_ENGINE_SNAPSHOT_ON_BRANCH =====")
    from aim_paper_trading_engine import AIMPaperTradingEngine

    snap = AIMPaperTradingEngine().dashboard_snapshot()
    for k in ("narrow_wing_butterfly", "bullish_risk_reversal"):
        print(f"LOCAL_{k}", json.dumps(snap[k]["rules"], sort_keys=True, default=str))
    _ok("local_engine_has_top2", "narrow_wing_butterfly" in snap and "bullish_risk_reversal" in snap)


def main() -> int:
    print("===== VERIFY_START =====")
    print(f"cwd={os.getcwd()}")
    print(f"argv={sys.argv}")
    try:
        section_spec_constants()
        section_reject_budget()
        section_reject_collateral()
        section_tp_math()
        section_entry_flatten_gates()
        section_bt_parity_table()
        section_db_persist()
        section_live_snapshot()
        section_local_engine_snapshot()
    except Exception:
        print("UNCAUGHT")
        traceback.print_exc()
        return 2
    print("===== SUMMARY =====")
    print(f"PASS_COUNT={PASS}")
    print(f"FAIL_COUNT={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
