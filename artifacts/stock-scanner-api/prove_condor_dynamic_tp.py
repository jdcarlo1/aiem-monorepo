#!/usr/bin/env python3
"""
Proof harness for Directive_CondorTP_UnreachableFix_2026-08-07.

1) TP math sanity: 4×D-style check replaced with dynamic target ≤ $500 plateau.
2) Entry-time code path invokes dynamic_tp_pct(priced D) — not a hardcoded fallback.
3) Paper fill of each condor via AsymOptionsLedger.evaluate with real priced D.
"""
from __future__ import annotations

import inspect
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")

# Confirmed bug debits from directive (entry 2026-08-05, Polygon daily)
CONFIRMED_D = {
    "call_condor": 167.0,
    "put_condor": 141.0,
}


def _session_df(y: int, m: int, d: int, spot: float) -> pd.DataFrame:
    ts = datetime(y, m, d, 9, 35, tzinfo=ET)
    return pd.DataFrame({"close": [spot]}, index=pd.DatetimeIndex([ts]))


def proof_1_math() -> bool:
    import aim_asym_paper_strategies as m

    print("===== PROOF_1_TP_MATH_DYNAMIC =====")
    print(f"MAX_PLATEAU_PAYOFF_USD={m.MAX_PLATEAU_PAYOFF_USD}")
    print(f"SAFETY_MARGIN={m.SAFETY_MARGIN}")
    all_ok = True
    for name, d in CONFIRMED_D.items():
        max_reachable_pct = (m.MAX_PLATEAU_PAYOFF_USD - d) / d * 100.0
        tp_pct = m.dynamic_tp_pct(d)
        # Target payoff (mark) to hit TP: D * (1 + tp/100)
        target_payoff = d * (1.0 + tp_pct / 100.0)
        # Old static 300% needed mark = 4×D (unreachable vs $500 plateau)
        static_300_mark = 4.0 * d
        le_plateau = target_payoff <= m.MAX_PLATEAU_PAYOFF_USD + 1e-9
        below_max = tp_pct < max_reachable_pct - 1e-9
        # Reachability: needed mark for TP ≤ theoretical max mark ($500)
        print(f"--- {name} ---")
        print(f"entry_debit_usd={d}")
        print(f"MAX_REACHABLE_TP_PCT_OF_ENTRY={max_reachable_pct:.6f}")
        print(f"dynamic_tp_pct={tp_pct:.6f}")
        print(f"computed_target_payoff_usd={target_payoff:.6f}")
        print(f"four_x_D_static300_mark={static_300_mark:.6f}")
        print(f"four_x_D_le_plateau_500={static_300_mark <= m.MAX_PLATEAU_PAYOFF_USD}")
        print(f"computed_target_le_plateau_500={le_plateau}")
        print(f"ASSERT_computed_target_payoff_le_500={le_plateau}")
        print(f"tp_below_MAX_REACHABLE={below_max}")
        if not (le_plateau and below_max and abs(tp_pct - m.SAFETY_MARGIN * max_reachable_pct) < 1e-9):
            all_ok = False
            print(f"FAIL {name}")
        else:
            print(f"PASS {name}")
    print(f"PROOF_1_OK={all_ok}")
    return all_ok


def proof_2_entry_path() -> bool:
    import aim_asym_paper_strategies as m

    print("===== PROOF_2_ENTRY_PATH_INVOKES_DYNAMIC_TP =====")
    src = inspect.getsource(m.AsymOptionsLedger.evaluate)
    checks = {
        "dynamic_set_gate": "self.strategy_key in DYNAMIC_PLATEAU_TP_STRATEGIES" in src,
        "calls_dynamic_tp_pct": "dynamic_tp_pct(per_pkg_debit)" in src,
        "per_pkg_from_entry_usd": "per_pkg_debit = float(entry_usd) / float(packages)" in src,
        "assigns_self_take_profit_pct": "self.take_profit_pct = float(dynamic_tp_pct(per_pkg_debit))" in src,
        "no_hardcoded_300_assign": "take_profit_pct = 300" not in src and "take_profit_pct = 300.0" not in src,
        "priced_via_polygon": "price_legs_polygon(" in src,
        "entry_usd_from_priced_debit": "unit_cost = debit_ps * 100.0" in src,
    }
    for k, v in checks.items():
        print(f"{k}={v}")

    # Extract the exact block for human review
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "DYNAMIC_PLATEAU_TP_STRATEGIES" in line and "strategy_key" in line:
            block = "\n".join(lines[i : i + 18])
            print("----- ENTRY_DYNAMIC_TP_BLOCK -----")
            print(block)
            print("----- END_BLOCK -----")
            break

    # Config ledger starts at 0.0 placeholder
    ledgers = m.build_default_asym_ledgers()
    print(f"call_condor_config_tp={ledgers['call_condor'].take_profit_pct}")
    print(f"put_condor_config_tp={ledgers['put_condor'].take_profit_pct}")
    cfg_ok = (
        ledgers["call_condor"].take_profit_pct == 0.0
        and ledgers["put_condor"].take_profit_pct == 0.0
    )
    print(f"config_placeholder_0={cfg_ok}")

    # Runtime: patch price_legs with confirmed D, prove evaluate overwrites TP
    def _fake_priced(debit_usd: float, right: str) -> dict:
        # debit_per_share = D/100
        k = 770.0
        if right == "call":
            legs = [
                {"qty": 1, "right": "call", "strike": k - 10, "premium": 0.0, "symbol": "C1"},
                {"qty": -1, "right": "call", "strike": k - 5, "premium": 0.0, "symbol": "C2"},
                {"qty": -1, "right": "call", "strike": k + 5, "premium": 0.0, "symbol": "C3"},
                {"qty": 1, "right": "call", "strike": k + 10, "premium": 0.0, "symbol": "C4"},
            ]
        else:
            legs = [
                {"qty": 1, "right": "put", "strike": k + 10, "premium": 0.0, "symbol": "P1"},
                {"qty": -1, "right": "put", "strike": k + 5, "premium": 0.0, "symbol": "P2"},
                {"qty": -1, "right": "put", "strike": k - 5, "premium": 0.0, "symbol": "P3"},
                {"qty": 1, "right": "put", "strike": k - 10, "premium": 0.0, "symbol": "P4"},
            ]
        return {
            "debit_per_share": debit_usd / 100.0,
            "legs": legs,
            "expiration": "2026-08-28",
            "pricing_source": "polygon_daily_option_aggs",
            "asof": "2026-08-05",
            "require_exact": True,
        }

    runtime_ok = True
    for strat, d, right, builder in (
        ("call_condor", 167.0, "call", m.build_long_call_condor),
        ("put_condor", 141.0, "put", m.build_long_put_condor),
    ):
        ledger = m.AsymOptionsLedger(
            f"LONG_{strat.upper()}",
            builder,
            0.0,
            strat,
            starting_capital_usd=10000.0,
        )
        expected = m.dynamic_tp_pct(d)
        with patch.object(m, "price_legs_polygon", return_value=_fake_priced(d, right)):
            with patch.object(m, "persist_asym_paper_open", return_value=9001) as pers:
                # Wednesday 2026-08-05
                ledger.evaluate(_session_df(2026, 8, 5, 769.79))
                # Prove persist received dynamic TP, not 300 / not 0
                kwargs = pers.call_args.kwargs if pers.call_args else {}
                persisted_tp = kwargs.get("take_profit_pct")
                pkgs = float(ledger.active_position["packages"]) if ledger.active_position else 0
                entry_usd = float(ledger.active_position["entry_debit_usd"]) if ledger.active_position else 0
                per_pkg = entry_usd / pkgs if pkgs else 0
                print(f"--- runtime_{strat} ---")
                print(f"priced_unit_debit_usd={d}")
                print(f"packages={pkgs}")
                print(f"entry_debit_usd={entry_usd}")
                print(f"per_pkg_debit_usd={per_pkg}")
                print(f"ledger_take_profit_pct_after_entry={ledger.take_profit_pct}")
                print(f"position_take_profit_pct={ledger.active_position and ledger.active_position.get('take_profit_pct')}")
                print(f"persist_take_profit_pct={persisted_tp}")
                print(f"expected_dynamic_tp={expected}")
                print(f"persist_called={pers.called}")
                ok = (
                    ledger.active_position is not None
                    and abs(float(ledger.take_profit_pct) - expected) < 1e-9
                    and abs(float(persisted_tp) - expected) < 1e-9
                    and abs(per_pkg - d) < 1e-6
                )
                print(f"runtime_path_ok={ok}")
                if not ok:
                    runtime_ok = False

    all_checks = all(checks.values()) and cfg_ok and runtime_ok
    print(f"PROOF_2_OK={all_checks}")
    return all_checks


def proof_3_paper_fills_real_polygon() -> bool:
    """Paper fill each condor with real Polygon daily pricing when available."""
    import aim_asym_paper_strategies as m

    print("===== PROOF_3_PAPER_FILLS =====")
    entry_day = date(2026, 8, 5)  # confirmed bug session (Wed)
    # Spot from prior directive evidence
    spot = 769.79
    exp = m.next_friday(entry_day, weeks_ahead=3)
    print(f"entry_day={entry_day.isoformat()} spot={spot} exp={exp.isoformat()}")

    fills = {}
    all_ok = True
    for strat, builder in (
        ("call_condor", m.build_long_call_condor),
        ("put_condor", m.build_long_put_condor),
    ):
        legs_spec = builder(spot)
        print(f"--- price_{strat} ---")
        print(f"legs_spec={legs_spec}")
        priced = m.price_legs_polygon(
            "SPY", exp, legs_spec, entry_day, require_exact=True
        )
        if not priced:
            print(f"POLYGON_PRICE_FAILED strategy={strat} — falling back to confirmed D inject")
            d = CONFIRMED_D[strat]
            # Still exercise evaluate with confirmed real D (from prior Polygon proof)
            debit_ps = d / 100.0
            priced = {
                "debit_per_share": debit_ps,
                "legs": [
                    {
                        "qty": q,
                        "right": r,
                        "strike": float(k),
                        "premium": 0.0,
                        "symbol": f"FALLBACK_{strat}_{i}",
                    }
                    for i, (q, r, k) in enumerate(legs_spec)
                ],
                "expiration": exp.isoformat(),
                "pricing_source": "confirmed_directive_debit_usd",
                "asof": entry_day.isoformat(),
                "require_exact": True,
            }
            pricing_mode = "confirmed_D_inject"
        else:
            d = float(priced["debit_per_share"]) * 100.0
            pricing_mode = "polygon_live_fetch"
            print(f"polygon_debit_usd={d:.4f}")
            print(f"priced_legs={[ (L['qty'], L['right'], L['strike'], L['premium']) for L in priced['legs'] ]}")

        expected_tp = m.dynamic_tp_pct(d)
        max_reachable = (m.MAX_PLATEAU_PAYOFF_USD - d) / d * 100.0
        target_payoff = d * (1.0 + expected_tp / 100.0)

        ledger = m.AsymOptionsLedger(
            f"LONG_{strat.upper()}",
            builder,
            0.0,
            strat,
            starting_capital_usd=10000.0,
        )
        with patch.object(m, "price_legs_polygon", return_value=priced):
            with patch.object(m, "persist_asym_paper_open", return_value=9100 + hash(strat) % 100) as pers:
                ledger.evaluate(_session_df(2026, 8, 5, spot))

        pos = ledger.active_position
        print(f"pricing_mode={pricing_mode}")
        print(f"signal_state={ledger.signal_state!r}")
        if not pos:
            print(f"FAIL_NO_FILL {strat}")
            all_ok = False
            continue
        tp_set = float(pos["take_profit_pct"])
        entry_usd = float(pos["entry_debit_usd"])
        pkgs = float(pos["packages"])
        per_pkg = entry_usd / pkgs
        below = tp_set < max_reachable - 1e-9
        le_plateau = target_payoff <= m.MAX_PLATEAU_PAYOFF_USD + 1e-9
        print(f"FILL strategy={strat}")
        print(f"packages={pkgs}")
        print(f"entry_debit_usd={entry_usd}")
        print(f"per_pkg_debit_usd={per_pkg}")
        print(f"take_profit_pct_set={tp_set}")
        print(f"MAX_REACHABLE_TP_PCT_OF_ENTRY={max_reachable:.6f}")
        print(f"tp_below_MAX_REACHABLE={below}")
        print(f"target_payoff_usd={target_payoff:.6f}")
        print(f"target_payoff_le_plateau={le_plateau}")
        print(f"persist_kwargs_tp={pers.call_args.kwargs.get('take_profit_pct')}")
        print(f"paper_trade_id={pos.get('paper_trade_id')}")
        ok = (
            abs(tp_set - expected_tp) < 1e-6
            and below
            and le_plateau
            and abs(per_pkg - d) < 0.02
        )
        print(f"fill_ok={ok}")
        fills[strat] = {
            "per_pkg_D": per_pkg,
            "packages": pkgs,
            "tp": tp_set,
            "max_reachable": max_reachable,
            "ok": ok,
            "pricing_mode": pricing_mode,
        }
        if not ok:
            all_ok = False

    print(f"PROOF_3_OK={all_ok}")
    print(f"FILLS={fills}")
    return all_ok


def main() -> int:
    p1 = proof_1_math()
    p2 = proof_2_entry_path()
    p3 = proof_3_paper_fills_real_polygon()
    print("===== SUMMARY =====")
    print(f"PROOF_1_OK={p1}")
    print(f"PROOF_2_OK={p2}")
    print(f"PROOF_3_OK={p3}")
    print(f"ALL_OK={p1 and p2 and p3}")
    print("BROKERAGE_GATE=do_not_proceed_until_deployed_live_fills_also_observed")
    return 0 if (p1 and p2 and p3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
