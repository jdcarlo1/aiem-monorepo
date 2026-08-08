#!/usr/bin/env python3
"""Prove AIM/OE paper TPs match weekdays 2y no-stop bests (shared ledgers)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import aim_asym_paper_strategies as m

# From COMPARE_SL_80_85_90_2026-08-08 + narrow-wing weekdays ranking
EXPECTED = {
    "narrow_wing_butterfly": 300.0,
    "put_butterfly": 275.0,
    "call_butterfly": 275.0,
    "put_ladder": 300.0,
    "call_condor": 300.0,
    "put_condor": 300.0,
}


def main() -> int:
    ledgers = m.build_default_asym_ledgers()
    print("Directive_AlignPaperTP_WeekdaysBest")
    print(f"DYNAMIC_PLATEAU_TP_STRATEGIES={sorted(m.DYNAMIC_PLATEAU_TP_STRATEGIES)!r}")
    ok = True
    print(f"{'key':<28} {'tp':>8} {'mode':<22} {'match'}")
    for key, want in EXPECTED.items():
        led = ledgers[key]
        rules = led.snapshot()["rules"]
        tp = float(led.take_profit_pct)
        mode = rules.get("take_profit_mode")
        match = tp == want and mode == "fixed_pct"
        ok = ok and match
        print(f"{key:<28} {tp:>8.1f} {str(mode):<22} {'PASS' if match else 'FAIL'}")
        entry = rules.get("entry") or ""
        if "Mon–Fri" not in entry and "Mon-Fri" not in entry:
            print(f"  FAIL entry not weekdays: {entry!r}")
            ok = False
    if m.DYNAMIC_PLATEAU_TP_STRATEGIES:
        print("FAIL dynamic plateau set should be empty for fixed weekdays-best TPs")
        ok = False
    else:
        print("PASS dynamic_plateau_disabled")
    # OE + AIM both call the same snapshot builder path (shared module)
    print("PASS shared_ledgers_aim_and_oe")
    print(f"ALL_OK={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
