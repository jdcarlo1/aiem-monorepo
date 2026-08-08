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

    print("FAIL_COUNT", fail)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
