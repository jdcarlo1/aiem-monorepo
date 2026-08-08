#!/usr/bin/env python3
"""
Top-6 weekday patterns: no-stop vs SL 80/85/90% of premium.

Same engine as spy_asymmetric_bt (--entry weekdays, 2y, $500 risk).
Compares live TP and weekday-best TP for each structure.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spy_asymmetric_bt as bt  # noqa: E402

# (strategy_key, live_tp, weekday_best_tp) — best TPs from prior weekdays nostop runs
TOP6 = [
    ("07_long_put_butterfly", 200, 275),
    ("06_long_call_butterfly", 100, 275),
    ("23_put_ladder_defined_risk", 150, 300),
    ("08_long_call_condor", 300, 300),
    ("09_long_put_condor", 300, 300),
    ("24_narrow_wing_call_butterfly", 200, 300),
]
SL_PCTS = [0.0, 80.0, 85.0, 90.0]
YEARS = 2.0
ARCHIVE = "spy-top6-sl-compare-weekdays"


def main() -> int:
    if not (os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY")):
        print("FATAL: POLYGON_API_KEY not set", file=sys.stderr)
        return 2

    end = date.today()
    start = end - timedelta(days=int(YEARS * 365.25))
    print(f"[sl-compare] {start}→{end} entry=weekdays SLs={SL_PCTS}")
    spy = bt.fetch_spy_daily(start, end)
    entries = bt.entry_dates_for_mode("weekdays", start, end)
    entries = [d for d in entries if d in spy.index or any(x >= d for x in spy.index)]
    print(f"[sl-compare] entry dates: {len(entries)}")

    arch = bt.archive_root(ARCHIVE)
    rows = []

    for sname, live_tp, best_tp in TOP6:
        builder = bt.STRATEGIES[sname]
        tps = sorted({int(live_tp), int(best_tp)})
        for tp in tps:
            baseline = None
            for sl in SL_PCTS:
                label = f"{sname}__tp{tp}" + ("" if sl <= 0 else f"__sl{int(sl)}")
                if sl <= 0:
                    label = f"{sname}__tp{tp}__nostop"
                print(f"\n=== {label} ===")
                trades = bt.run_strategy(sname, builder, spy, entries, tp, end, sl_pct=sl)
                summary = bt.summarize(trades)
                print(
                    f"  trades={summary['trades']} pnl=${summary['total_pnl']} "
                    f"wr={summary['win_rate']} sl_hits={summary.get('sl_hits')} "
                    f"tp_hits={summary.get('tp_hits')}"
                )
                out = {
                    "label": label,
                    "strategy": sname,
                    "tp_pct": tp,
                    "sl_pct": sl,
                    "tp_role": (
                        "live_and_best"
                        if live_tp == best_tp == tp
                        else ("live" if tp == live_tp else "weekday_best")
                    ),
                    **summary,
                }
                if sl <= 0:
                    baseline = out
                else:
                    if baseline and baseline["trades"]:
                        out["delta_pnl_vs_nostop"] = round(
                            out["total_pnl"] - baseline["total_pnl"], 2
                        )
                        out["delta_wr_vs_nostop"] = round(
                            out["win_rate"] - baseline["win_rate"], 4
                        )
                        out["saves_money"] = out["delta_pnl_vs_nostop"] > 0
                    else:
                        out["delta_pnl_vs_nostop"] = None
                        out["saves_money"] = None
                rows.append(out)
                path = arch / f"{label}.json"
                path.write_text(
                    json.dumps({"summary": out, "trades": trades}, indent=2, default=str)
                )

    # Compact compare table
    compare = {
        "rules": {
            "underlying": "SPY",
            "risk_usd": bt.RISK_USD,
            "entry": "every Mon–Fri weekday",
            "years": YEARS,
            "sl_grid_pct_of_premium": SL_PCTS,
            "note": (
                "SL N% exits when package P&L <= -N% of |entry| premium; "
                "stop checked before TP on same bar (engine default)."
            ),
            "strategies": [
                {
                    "strategy": s,
                    "live_tp": live,
                    "weekday_best_tp": best,
                }
                for s, live, best in TOP6
            ],
        },
        "rows": rows,
        "verdict_by_combo": [],
    }
    for sname, live_tp, best_tp in TOP6:
        for tp in sorted({int(live_tp), int(best_tp)}):
            nostop = next(
                r
                for r in rows
                if r["strategy"] == sname and r["tp_pct"] == tp and r["sl_pct"] <= 0
            )
            for sl in (80.0, 85.0, 90.0):
                r = next(
                    x
                    for x in rows
                    if x["strategy"] == sname and x["tp_pct"] == tp and x["sl_pct"] == sl
                )
                compare["verdict_by_combo"].append(
                    {
                        "strategy": sname,
                        "tp_pct": tp,
                        "sl_pct": sl,
                        "nostop_pnl": nostop["total_pnl"],
                        "with_sl_pnl": r["total_pnl"],
                        "delta_pnl": r.get("delta_pnl_vs_nostop"),
                        "saves_money": r.get("saves_money"),
                        "nostop_wr": nostop["win_rate"],
                        "with_sl_wr": r["win_rate"],
                        "sl_hits": r.get("sl_hits"),
                    }
                )

    any_save = any(v.get("saves_money") for v in compare["verdict_by_combo"])
    compare["overall"] = {
        "any_sl_beats_nostop": any_save,
        "combos_where_sl_helps": sum(
            1 for v in compare["verdict_by_combo"] if v.get("saves_money")
        ),
        "combos_tested": len(compare["verdict_by_combo"]),
    }

    out_path = arch / f"COMPARE_SL_80_85_90_{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(compare, indent=2))
    print(f"\n[sl-compare] wrote {out_path}")
    print(json.dumps(compare["overall"], indent=2))
    print("\n===== DELTA PnL (SL − nostop); positive = stop helps =====")
    for v in compare["verdict_by_combo"]:
        dlt = v["delta_pnl"]
        flag = "HELPS" if v["saves_money"] else "HURTS"
        print(
            f"{flag:5} {v['strategy']:32} TP{v['tp_pct']:>3} SL{int(v['sl_pct'])}  "
            f"Δ ${dlt:>12,.0f}  nostop ${v['nostop_pnl']:>12,.0f} → "
            f"${v['with_sl_pnl']:>12,.0f}  sl_hits={v['sl_hits']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
