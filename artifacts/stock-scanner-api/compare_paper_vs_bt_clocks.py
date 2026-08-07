#!/usr/bin/env python3
"""
Compare paper-live lookback fills vs catalog-BT Monday daily asof fills
for Narrow-Wing Butterfly (+200%) and Bullish Risk Reversal (+75%).

Methods:
  BT_ASOF     — entry priced with Polygon daily asof Monday (catalog BT)
  PAPER_0930  — entry priced with last daily STRICTLY BEFORE Monday
                (simulates live 09:30 Mon before Monday daily settles)
  PAPER_EXACT — entry priced only if exact Monday daily bars exist
                (proposed paper alignment to BT)

Exit path identical for all: daily session walk, TP on |entry|, else
last daily <= expiry Friday (catalog EXPIRY_FLATTEN). No stop.

Usage:
  POLYGON_API_KEY=... python3 compare_paper_vs_bt_clocks.py [--max-entries N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import spy_asymmetric_bt as bt  # noqa: E402

OUT_DIR = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "verification"
    / "patternlab-top2-wiring-2026-08-07"
)

RISK_USD = 500.0


def b_narrow(spot, d0, en, ef):
    k = float(round(spot))
    return [(1, "C", k - 2, en), (-2, "C", k, en), (1, "C", k + 2, en)]


def b_rr(spot, d0, en, ef):
    k = float(round(spot))
    return [(1, "C", k + 5, en), (-1, "P", k - 5, en)]


STRATS = {
    "narrow_wing_butterfly": (b_narrow, 200.0),
    "bullish_risk_reversal": (b_rr, 75.0),
}


def _px_exact(series: pd.Series, d: date):
    if series is None or series.empty or d not in series.index:
        return None
    px = series.loc[d]
    if px is None or (isinstance(px, float) and np.isnan(px)) or float(px) <= 0:
        return None
    return float(px)


def _px_before(series: pd.Series, d: date):
    """Last bar strictly before d (paper 09:30 Mon before Monday settles)."""
    if series is None or series.empty:
        return None
    prior = series[series.index < d]
    if prior.empty:
        return None
    px = float(prior.iloc[-1])
    return px if px > 0 else None


def _package(legs, d, mode: str):
    total = 0.0
    for leg in legs:
        if mode == "BT_ASOF":
            px = bt._px_on(leg.series, d)
        elif mode == "PAPER_0930":
            px = _px_before(leg.series, d)
        elif mode == "PAPER_EXACT":
            px = _px_exact(leg.series, d)
        else:
            raise ValueError(mode)
        if px is None:
            return None
        total += leg.qty * px * 100.0
    return total


def run_one(name, builder, spy, d0, tp_pct, end, mode: str):
    if d0 not in spy.index:
        later = [x for x in spy.index if x >= d0]
        if not later:
            return None
        d0 = later[0]
    spot = float(spy.loc[d0, "close"])
    exp = bt.next_friday(d0, weeks_ahead=3)
    if exp > end + timedelta(days=7):
        return None
    raw = builder(spot, d0, exp, exp)
    unique = {}
    legs = []
    for qty, right, strike, e in raw:
        sym = bt._occ(d0, strike, right, e)
        if sym not in unique:
            unique[sym] = bt.fetch_option_daily(sym, d0 - timedelta(days=10), min(e, end))
        ser = unique[sym]
        # Need some history for PAPER_0930 prior bar
        if mode == "BT_ASOF" and bt._px_on(ser, d0) is None:
            return None
        if mode == "PAPER_EXACT" and _px_exact(ser, d0) is None:
            return None
        if mode == "PAPER_0930" and _px_before(ser, d0) is None:
            return None
        legs.append(bt.LegPos(qty=qty, symbol=sym, series=ser))

    entry_val = _package(legs, d0, mode)
    if entry_val is None or abs(entry_val) < 1.0:
        return None
    if entry_val > 0:
        if entry_val > RISK_USD:
            return None
        mult = max(int(RISK_USD / entry_val), 1)
        while mult > 1 and entry_val * mult > RISK_USD:
            mult -= 1
    else:
        mult = 1
    for lp in legs:
        lp.qty *= mult
    entry_val *= mult
    tp_dollars = abs(entry_val) * (tp_pct / 100.0)

    hold_end = min(exp, end)
    sessions = [x for x in spy.index if d0 < x <= hold_end]
    exit_d = exit_val = exit_reason = pnl = None
    for d in sessions:
        # Exit marks always use BT asof daily (shared flatten methodology)
        mark = bt.package_value(legs, d)
        if mark is None:
            continue
        pnl_now = mark - entry_val
        if pnl_now >= tp_dollars:
            exit_d, exit_val, exit_reason, pnl = d, mark, f"TP_{int(tp_pct)}PCT", pnl_now
            break
    if exit_d is None:
        for d in reversed(sessions):
            mark = bt.package_value(legs, d)
            if mark is None:
                continue
            pnl = mark - entry_val
            exit_d, exit_val, exit_reason = d, mark, "EXPIRY_FLATTEN"
            break
        if exit_d is None:
            return None
    return {
        "strategy": name,
        "mode": mode,
        "tp_pct": tp_pct,
        "entry_date": d0.isoformat(),
        "exit_date": exit_d.isoformat(),
        "entry_val": round(float(entry_val), 2),
        "exit_val": round(float(exit_val), 2),
        "pnl": round(float(pnl), 2),
        "exit_reason": exit_reason,
        "packages": mult,
        "spot": round(spot, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-06")
    ap.add_argument("--end", default="2025-07-31")
    ap.add_argument("--max-entries", type=int, default=20)
    args = ap.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if not (os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY")):
        print("CANNOT PRODUCE: POLYGON_API_KEY unset")
        return 2

    print("===== CLOCK_COMPARE_START =====")
    print(f"window={start}→{end} max_entries={args.max_entries}")
    spy = bt.fetch_spy_daily(start - timedelta(days=14), end)
    mondays = [d for d in spy.index if d.weekday() == 0 and start <= d <= end]
    if args.max_entries:
        mondays = mondays[: args.max_entries]
    print(f"mondays={len(mondays)} first={mondays[0] if mondays else None} last={mondays[-1] if mondays else None}")

    modes = ("BT_ASOF", "PAPER_0930", "PAPER_EXACT")
    rows = []
    for name, (builder, tp) in STRATS.items():
        for mode in modes:
            for d0 in mondays:
                t = run_one(name, builder, spy, d0, tp, end, mode)
                if t:
                    rows.append(t)
                    print(
                        f"TRADE {name} {mode} {t['entry_date']} entry={t['entry_val']} "
                        f"exit={t['exit_val']} pnl={t['pnl']} reason={t['exit_reason']}"
                    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = OUT_DIR / "CLOCK_COMPARE_TRADES.jsonl"
    with raw_path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # Pairwise vs BT_ASOF on same entry_date+strategy
    by = {}
    for r in rows:
        by.setdefault((r["strategy"], r["entry_date"]), {})[r["mode"]] = r

    print("===== PAIRWISE_VS_BT_ASOF =====")
    summary = {}
    for (strat, d0), m in sorted(by.items()):
        if "BT_ASOF" not in m:
            continue
        bt_row = m["BT_ASOF"]
        for mode in ("PAPER_0930", "PAPER_EXACT"):
            if mode not in m:
                print(f"MISSING_PAIR {strat} {d0} {mode}")
                continue
            o = m[mode]
            de = round(o["entry_val"] - bt_row["entry_val"], 2)
            dp = round(o["pnl"] - bt_row["pnl"], 2)
            same_exit = o["exit_date"] == bt_row["exit_date"] and o["exit_reason"] == bt_row["exit_reason"]
            print(
                f"DELTA {strat} {d0} {mode}: d_entry={de} d_pnl={dp} "
                f"same_exit={same_exit} bt_pnl={bt_row['pnl']} alt_pnl={o['pnl']}"
            )
            s = summary.setdefault(f"{strat}|{mode}", {
                "n": 0, "entry_diff_sum": 0.0, "pnl_diff_sum": 0.0,
                "entry_diff_abs_sum": 0.0, "pnl_diff_abs_sum": 0.0,
                "same_exit": 0, "exact_match": 0,
            })
            s["n"] += 1
            s["entry_diff_sum"] += de
            s["pnl_diff_sum"] += dp
            s["entry_diff_abs_sum"] += abs(de)
            s["pnl_diff_abs_sum"] += abs(dp)
            s["same_exit"] += 1 if same_exit else 0
            s["exact_match"] += 1 if de == 0 and dp == 0 and same_exit else 0

    print("===== SUMMARY =====")
    out_summary = {}
    for k, s in summary.items():
        n = max(s["n"], 1)
        rec = {
            **s,
            "mean_abs_entry_diff": round(s["entry_diff_abs_sum"] / n, 4),
            "mean_abs_pnl_diff": round(s["pnl_diff_abs_sum"] / n, 4),
            "mean_pnl_diff": round(s["pnl_diff_sum"] / n, 4),
            "exact_match_pct": round(100.0 * s["exact_match"] / n, 2),
            "same_exit_pct": round(100.0 * s["same_exit"] / n, 2),
        }
        out_summary[k] = rec
        print(k, json.dumps(rec, sort_keys=True))

    sum_path = OUT_DIR / "CLOCK_COMPARE_SUMMARY.json"
    sum_path.write_text(json.dumps({
        "window": {"start": str(start), "end": str(end), "mondays": len(mondays)},
        "decision": {
            "material_divergence": any(
                v["mean_abs_pnl_diff"] > 1.0 or v["exact_match_pct"] < 100.0
                for k, v in out_summary.items() if k.endswith("|PAPER_0930")
            ),
            "paper_must_use": "BT_ASOF / PAPER_EXACT (Monday daily close; no pre-settle lookback fill)",
            "reject": "PAPER_0930 live lookback (prior session premium at Mon 09:30)",
        },
        "summary": out_summary,
    }, indent=2))
    print(f"wrote {raw_path}")
    print(f"wrote {sum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
