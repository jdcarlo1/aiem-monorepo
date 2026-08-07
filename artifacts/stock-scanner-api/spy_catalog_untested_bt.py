#!/usr/bin/env python3
"""
SPY Catalog Untested Strategies — same rules as spy_asymmetric_bt.py

Directive: backtest every catalog strategy NOT already covered by the 23-strategy
asymmetric BT, using real Polygon daily option aggregates.

Shared rules (identical to prior run):
  - Underlying: SPY
  - Risk budget: $500 max debit (credits: 1 package)
  - Entry: weekly Monday
  - Exit: TP grid 50/75/100/125/150/200% of |entry|; NO STOP; else expiry flatten
  - Pricing: Polygon daily option aggregates (O:SPY…) — no synthetic BS

Archives: docs/verification/spy-catalog-untested-bt/

Usage:
  POLYGON_API_KEY=... python3 spy_catalog_untested_bt.py
  POLYGON_API_KEY=... python3 spy_catalog_untested_bt.py --max-entries 4   # smoke
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

# Reuse proven engine pieces from the 23-strategy BT
sys.path.insert(0, str(Path(__file__).resolve().parent))
import spy_asymmetric_bt as bt  # noqa: E402

ARCHIVE_DIR_NAME = "spy-catalog-untested-bt"
RISK_USD = float(os.environ.get("ASYM_BT_RISK_USD", "500"))
TP_PCTS = [50, 75, 100, 125, 150, 200]

# Already covered by 23-strategy BT (exact catalog names) — skip duplicates
ALREADY_TESTED = {
    "Long Call",
    "Long Put",
    "Bull Call Debit Spread",
    "Bear Put Debit Spread",
    "Broken-Wing Call Butterfly",
    "Long Call Butterfly",
    "Long Put Butterfly",
    "Call Backspread 2x1",
    "Put Backspread 2x1",
    "Long Straddle",
    "Long Strangle",
    "Long Call Calendar",
    "Long Put Calendar",
    "Long Call Diagonal Bullish",
    "Debit Call Diagonal",
    "Long Put Diagonal Bearish",
    "Unbalanced Call Butterfly",
    "Skip-Strike Call Butterfly",
    "Christmas Tree Call",
}


def archive_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    p = root / "docs" / "verification" / ARCHIVE_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _k(spot: float, step: float = 1.0) -> float:
    return float(round(spot / step) * step)


# ── builders: (qty, right, strike, exp)  right in C/P ─────────────────────────
# Stock legs: (qty_shares, "STK", 0.0, exp_near) — marked with SPY close

def _stk(shares: int, exp):
    return (shares, "STK", 0.0, exp)


def b_cash_secured_put(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k, en)]  # short put; CSP collateral not modeled as cash


def b_covered_short_call(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (-1, "C", k, en)]


def b_leaps_call(spot, d0, en, ef):
    # ~1y LEAPS proxy: far Friday + 45 weeks
    leaps = bt.next_friday(d0, weeks_ahead=45)
    return [(1, "C", _k(spot), leaps)]


def b_leaps_put(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    return [(1, "P", _k(spot), leaps)]


def b_deep_itm_call(spot, d0, en, ef):
    return [(1, "C", _k(spot) - 20, en)]


def b_deep_itm_put(spot, d0, en, ef):
    return [(1, "P", _k(spot) + 20, en)]


def b_covered_call(spot, d0, en, ef):
    return [_stk(100, en), (-1, "C", _k(spot) + 5, en)]


def b_buy_write(spot, d0, en, ef):
    return [_stk(100, en), (-1, "C", _k(spot), en)]


def b_covered_put(spot, d0, en, ef):
    return [_stk(-100, en), (-1, "P", _k(spot) - 5, en)]


def b_protective_put(spot, d0, en, ef):
    return [_stk(100, en), (1, "P", _k(spot) - 5, en)]


def b_protective_call(spot, d0, en, ef):
    return [_stk(-100, en), (1, "C", _k(spot) + 5, en)]


def b_married_put(spot, d0, en, ef):
    return [_stk(100, en), (1, "P", _k(spot), en)]


def b_collar(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (1, "P", k - 5, en), (-1, "C", k + 5, en)]


def b_zero_cost_collar(spot, d0, en, ef):
    # approx same structure; pricing decides credit/debit
    k = _k(spot)
    return [_stk(100, en), (1, "P", k - 5, en), (-1, "C", k + 10, en)]


def b_put_spread_collar(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (1, "P", k - 5, en), (-1, "P", k - 15, en), (-1, "C", k + 10, en)]


def b_seagull_collar(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (-1, "C", k + 5, en), (1, "P", k - 5, en), (-1, "P", k - 15, en)]


def b_covered_strangle(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (-1, "C", k + 5, en), (-1, "P", k - 5, en)]


def b_pmcc(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(1, "C", k - 20, leaps), (-1, "C", k + 5, en)]


def b_pmcp(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(1, "P", k + 20, leaps), (-1, "P", k - 5, en)]


def b_synth_covered_call(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k - 20, en), (-1, "C", k + 5, en)]


def b_synth_covered_put(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k + 20, en), (-1, "P", k - 5, en)]


def b_stock_repair(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (1, "C", k, en), (-2, "C", k + 5, en)]


def b_wheel(spot, d0, en, ef):
    # CSP phase proxy (wheel start)
    return [(-1, "P", _k(spot) - 5, en)]


def b_dynamic_collar(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (1, "P", k - 10, en), (-1, "C", k + 5, en)]


def b_bull_call_itm(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k - 5, en), (-1, "C", k, en)]


def b_bull_call_otm(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k + 5, en), (-1, "C", k + 10, en)]


def b_bear_call_credit(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k + 5, en), (1, "C", k + 10, en)]


def b_bear_call_credit_otm(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k + 10, en), (1, "C", k + 15, en)]


def b_narrow_bull_call(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k + 2, en)]


def b_wide_bull_call(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k + 15, en)]


def b_leaps_bull_call(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(1, "C", k, leaps), (-1, "C", k + 20, leaps)]


def b_weekly_bear_call(spot, d0, en, ef):
    w = bt.next_friday(d0, weeks_ahead=0)
    if w <= d0:
        w = bt.next_friday(d0, weeks_ahead=1)
    k = _k(spot)
    return [(-1, "C", k + 5, w), (1, "C", k + 10, w)]


def b_call_roll_up_out(spot, d0, en, ef):
    # proxy: debit vertical that would be rolled — single vertical
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k + 10, ef)]


def b_bear_put_itm(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k + 5, en), (-1, "P", k, en)]


def b_bear_put_otm(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k - 5, en), (-1, "P", k - 10, en)]


def b_bull_put_credit(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 5, en), (1, "P", k - 10, en)]


def b_bull_put_credit_otm(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 10, en), (1, "P", k - 15, en)]


def b_narrow_bear_put(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-1, "P", k - 2, en)]


def b_wide_bear_put(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-1, "P", k - 15, en)]


def b_leaps_bear_put(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(1, "P", k, leaps), (-1, "P", k - 20, leaps)]


def b_weekly_bull_put(spot, d0, en, ef):
    w = bt.next_friday(d0, weeks_ahead=0)
    if w <= d0:
        w = bt.next_friday(d0, weeks_ahead=1)
    k = _k(spot)
    return [(-1, "P", k - 5, w), (1, "P", k - 10, w)]


def b_put_roll_down_out(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-1, "P", k - 10, ef)]


def b_bull_rr(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k + 5, en), (-1, "P", k - 5, en)]


def b_bear_rr(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k - 5, en), (-1, "C", k + 5, en)]


def b_synth_long(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "P", k, en)]


def b_synth_short(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (1, "P", k, en)]


def b_split_synth_bull(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k + 5, en), (-1, "P", k - 5, en)]


def b_split_synth_bear(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k - 5, en), (-1, "C", k + 5, en)]


def b_double_bull(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k + 5, en), (-1, "P", k - 5, en), (1, "P", k - 10, en)]


def b_double_bear(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-1, "P", k - 5, en), (-1, "C", k + 5, en), (1, "C", k + 10, en)]


def b_call_cal_itm(spot, d0, en, ef):
    k = _k(spot) - 5
    return [(-1, "C", k, en), (1, "C", k, ef)]


def b_call_cal_otm(spot, d0, en, ef):
    k = _k(spot) + 5
    return [(-1, "C", k, en), (1, "C", k, ef)]


def b_short_call_cal(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k, ef)]


def b_double_cal(spot, d0, en, ef):
    k = _k(spot)
    return [
        (-1, "C", k + 5, en), (1, "C", k + 5, ef),
        (-1, "P", k - 5, en), (1, "P", k - 5, ef),
    ]


def b_earnings_cal(spot, d0, en, ef):
    return b_double_cal(spot, d0, en, ef)


def b_leaps_cal_call(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(-1, "C", k, en), (1, "C", k, leaps)]


def b_leaps_cal_put(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(-1, "P", k, en), (1, "P", k, leaps)]


def b_ratio_cal(spot, d0, en, ef):
    k = _k(spot)
    return [(-2, "C", k, en), (1, "C", k, ef)]


def b_cal_vertical(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (1, "C", k + 5, ef)]


def b_reverse_cal(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k, ef)]


def b_credit_call_diag(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k + 5, en), (-1, "C", k, ef)]


def b_double_diag(spot, d0, en, ef):
    k = _k(spot)
    return [
        (-1, "C", k + 5, en), (1, "C", k + 10, ef),
        (-1, "P", k - 5, en), (1, "P", k - 10, ef),
    ]


def b_leaps_diag_call(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(-1, "C", k + 5, en), (1, "C", k, leaps)]


def b_leaps_diag_put(spot, d0, en, ef):
    leaps = bt.next_friday(d0, weeks_ahead=45)
    k = _k(spot)
    return [(-1, "P", k - 5, en), (1, "P", k, leaps)]


def b_earnings_diag(spot, d0, en, ef):
    return b_double_diag(spot, d0, en, ef)


def b_bwb_call_diag(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (1, "C", k - 5, ef), (1, "C", k + 15, ef)]


def b_bwb_put_diag(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k, en), (1, "P", k + 5, ef), (1, "P", k - 15, ef)]


def b_diag_straddle(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (-1, "P", k, en), (1, "C", k, ef), (1, "P", k, ef)]


def b_rolling_diag(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (1, "C", k + 5, ef)]


def b_ratio_diag(spot, d0, en, ef):
    k = _k(spot)
    return [(-2, "C", k + 5, en), (1, "C", k, ef)]


def b_short_call_diag(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k + 5, ef)]


def b_short_straddle(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (-1, "P", k, en)]


def b_short_strangle(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k + 5, en), (-1, "P", k - 5, en)]


def b_strap(spot, d0, en, ef):
    k = _k(spot)
    return [(2, "C", k, en), (1, "P", k, en)]


def b_strip(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (2, "P", k, en)]


def b_iron_straddle(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (-1, "P", k, en), (1, "C", k + 10, en), (1, "P", k - 10, en)]


def b_rev_ratio_straddle(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (-1, "P", k, en), (2, "C", k + 10, en)]


def b_cal_straddle(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (-1, "P", k, en), (1, "C", k, ef), (1, "P", k, ef)]


def b_diag_strangle(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k + 5, en), (-1, "P", k - 5, en), (1, "C", k + 5, ef), (1, "P", k - 5, ef)]


def b_iron_fly(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (-1, "P", k, en), (1, "C", k + 5, en), (1, "P", k - 5, en)]


def b_bwb_put(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-2, "P", k - 5, en), (1, "P", k - 15, en)]


def b_wide_wing_fly(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k - 10, en), (-2, "C", k, en), (1, "C", k + 10, en)]


def b_narrow_wing_fly(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k - 2, en), (-2, "C", k, en), (1, "C", k + 2, en)]


def b_double_fly(spot, d0, en, ef):
    k = _k(spot)
    return [
        (1, "C", k - 5, en), (-2, "C", k, en), (1, "C", k + 5, en),
        (1, "P", k + 5, en), (-2, "P", k, en), (1, "P", k - 5, en),
    ]


def b_reverse_fly(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k - 5, en), (2, "C", k, en), (-1, "C", k + 5, en)]


def b_cal_fly(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k - 5, en), (2, "C", k, en), (-1, "C", k + 5, en),
            (1, "C", k - 5, ef), (-2, "C", k, ef), (1, "C", k + 5, ef)]


def b_earnings_fly(spot, d0, en, ef):
    return bt.build_long_call_fly(spot, d0, en, ef)


def b_iron_condor(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 5, en), (1, "P", k - 10, en), (-1, "C", k + 5, en), (1, "C", k + 10, en)]


def b_ic_narrow(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 3, en), (1, "P", k - 5, en), (-1, "C", k + 3, en), (1, "C", k + 5, en)]


def b_ic_wide(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 10, en), (1, "P", k - 20, en), (-1, "C", k + 10, en), (1, "C", k + 20, en)]


def b_rev_ic(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k - 5, en), (-1, "P", k - 10, en), (1, "C", k + 5, en), (-1, "C", k + 10, en)]


def b_bwb_ic(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 5, en), (1, "P", k - 15, en), (-1, "C", k + 5, en), (1, "C", k + 10, en)]


def b_asym_ic(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 5, en), (1, "P", k - 10, en), (-1, "C", k + 5, en), (1, "C", k + 15, en)]


def b_double_condor(spot, d0, en, ef):
    k = _k(spot)
    return [
        (-1, "P", k - 5, en), (1, "P", k - 10, en), (-1, "C", k + 5, en), (1, "C", k + 10, en),
        (-1, "P", k - 5, ef), (1, "P", k - 10, ef), (-1, "C", k + 5, ef), (1, "C", k + 10, ef),
    ]


def b_skewed_ic(spot, d0, en, ef):
    return b_asym_ic(spot, d0, en, ef)


def b_dn_ic(spot, d0, en, ef):
    return b_iron_condor(spot, d0, en, ef)


def b_earn_ic(spot, d0, en, ef):
    return b_iron_condor(spot, d0, en, ef)


def b_0dte_ic(spot, d0, en, ef):
    w = bt.next_friday(d0, weeks_ahead=0)
    if w <= d0:
        w = bt.next_friday(d0, weeks_ahead=1)
    return b_iron_condor(spot, d0, w, ef)


def b_0dte_ifly(spot, d0, en, ef):
    w = bt.next_friday(d0, weeks_ahead=0)
    if w <= d0:
        w = bt.next_friday(d0, weeks_ahead=1)
    return b_iron_fly(spot, d0, w, ef)


def b_call_ratio_1x2(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-2, "C", k + 5, en)]


def b_put_ratio_1x2(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-2, "P", k - 5, en)]


def b_call_ratio_1x3(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-3, "C", k + 5, en)]


def b_put_ratio_1x3(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-3, "P", k - 5, en)]


def b_bull_seagull(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-1, "C", k + 5, en), (-1, "P", k - 10, en)]


def b_bear_seagull(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-1, "P", k - 5, en), (-1, "C", k + 10, en)]


def b_cov_ratio_call(spot, d0, en, ef):
    k = _k(spot)
    return [_stk(100, en), (-2, "C", k + 5, en)]


def b_zc_call_ratio(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k - 5, en), (-2, "C", k + 5, en)]


def b_bwb_call_ratio(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (-2, "C", k + 5, en), (1, "C", k + 15, en)]


def b_bwb_put_ratio(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k, en), (-2, "P", k - 5, en), (1, "P", k - 15, en)]


def b_jade_lizard(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k - 5, en), (1, "P", k - 10, en), (-1, "C", k + 5, en)]


def b_rev_jade(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k + 5, en), (1, "C", k + 10, en), (-1, "P", k - 5, en)]


def b_big_lizard(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "C", k, en), (-1, "P", k - 5, en), (1, "P", k - 10, en)]


def b_twisted_sister(spot, d0, en, ef):
    k = _k(spot)
    return [(-1, "P", k, en), (-1, "C", k + 5, en), (1, "C", k + 10, en)]


def b_rev_iron_fly(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "C", k, en), (1, "P", k, en), (-1, "C", k + 5, en), (-1, "P", k - 5, en)]


def b_crash_put_spread(spot, d0, en, ef):
    k = _k(spot)
    return [(1, "P", k - 20, en), (-1, "P", k - 40, en)]


def b_tail_risk(spot, d0, en, ef):
    return b_crash_put_spread(spot, d0, en, ef)


def b_0dte_vertical(spot, d0, en, ef):
    w = bt.next_friday(d0, weeks_ahead=0)
    if w <= d0:
        w = bt.next_friday(d0, weeks_ahead=1)
    k = _k(spot)
    return [(1, "C", k, w), (-1, "C", k + 5, w)]


def b_weekly_strangle_event(spot, d0, en, ef):
    w = bt.next_friday(d0, weeks_ahead=0)
    if w <= d0:
        w = bt.next_friday(d0, weeks_ahead=1)
    k = _k(spot)
    return [(1, "C", k + 5, w), (1, "P", k - 5, w)]


def b_overnight_gap_hedge(spot, d0, en, ef):
    w = bt.next_friday(d0, weeks_ahead=0)
    if w <= d0:
        w = bt.next_friday(d0, weeks_ahead=1)
    return [(1, "P", _k(spot) - 5, w)]


def b_earn_long_straddle(spot, d0, en, ef):
    return bt.build_long_straddle(spot, d0, en, ef)


def b_earn_long_strangle(spot, d0, en, ef):
    return bt.build_long_strangle(spot, d0, en, ef)


# Catalog name → builder. Abstract/non-structure names omitted → SKIPPED list.
STRATEGIES: dict[str, Callable] = {
    "Cash-Secured Put": b_cash_secured_put,
    "Covered Short Call": b_covered_short_call,
    "LEAPS Call": b_leaps_call,
    "LEAPS Put": b_leaps_put,
    "Deep-ITM Call": b_deep_itm_call,
    "Deep-ITM Put": b_deep_itm_put,
    "Covered Call": b_covered_call,
    "Buy-Write": b_buy_write,
    "Covered Put": b_covered_put,
    "Protective Put": b_protective_put,
    "Protective Call": b_protective_call,
    "Married Put": b_married_put,
    "Collar": b_collar,
    "Zero-Cost Collar": b_zero_cost_collar,
    "Put-Spread Collar": b_put_spread_collar,
    "Seagull Collar": b_seagull_collar,
    "Covered Strangle": b_covered_strangle,
    "Poor Man's Covered Call": b_pmcc,
    "Poor Man's Covered Put": b_pmcp,
    "Synthetic Covered Call": b_synth_covered_call,
    "Synthetic Covered Put": b_synth_covered_put,
    "Stock Repair": b_stock_repair,
    "Wheel Strategy": b_wheel,
    "Dynamic Collar": b_dynamic_collar,
    "Bull Call Debit Spread ITM": b_bull_call_itm,
    "Bull Call Debit Spread OTM": b_bull_call_otm,
    "Bear Call Credit Spread": b_bear_call_credit,
    "Bear Call Credit Spread OTM": b_bear_call_credit_otm,
    "Narrow Bull Call Spread": b_narrow_bull_call,
    "Wide Bull Call Spread": b_wide_bull_call,
    "LEAPS Bull Call Spread": b_leaps_bull_call,
    "Weekly Bear Call Spread": b_weekly_bear_call,
    "Call Spread Roll-Up-Out": b_call_roll_up_out,
    "Bear Put Debit Spread ITM": b_bear_put_itm,
    "Bear Put Debit Spread OTM": b_bear_put_otm,
    "Bull Put Credit Spread": b_bull_put_credit,
    "Bull Put Credit Spread OTM": b_bull_put_credit_otm,
    "Narrow Bear Put Spread": b_narrow_bear_put,
    "Wide Bear Put Spread": b_wide_bear_put,
    "LEAPS Bear Put Spread": b_leaps_bear_put,
    "Weekly Bull Put Spread": b_weekly_bull_put,
    "Put Spread Roll-Down-Out": b_put_roll_down_out,
    "Bullish Risk Reversal": b_bull_rr,
    "Bearish Risk Reversal": b_bear_rr,
    "Synthetic Long Stock": b_synth_long,
    "Synthetic Short Stock": b_synth_short,
    "Split-Strike Synthetic Bullish": b_split_synth_bull,
    "Split-Strike Synthetic Bearish": b_split_synth_bear,
    "Double Bull Spread": b_double_bull,
    "Double Bear Spread": b_double_bear,
    "Long Call Calendar ITM": b_call_cal_itm,
    "Long Call Calendar OTM": b_call_cal_otm,
    "Short Call Calendar": b_short_call_cal,
    "Double Calendar": b_double_cal,
    "Earnings Calendar": b_earnings_cal,
    "LEAPS Calendar Call": b_leaps_cal_call,
    "LEAPS Calendar Put": b_leaps_cal_put,
    "Ratio Calendar": b_ratio_cal,
    "Calendarized Vertical": b_cal_vertical,
    "Reverse Calendar": b_reverse_cal,
    "Credit Call Diagonal": b_credit_call_diag,
    "Double Diagonal": b_double_diag,
    "LEAPS Diagonal Call": b_leaps_diag_call,
    "LEAPS Diagonal Put": b_leaps_diag_put,
    "Earnings Diagonal": b_earnings_diag,
    "Broken-Wing Call Diagonal": b_bwb_call_diag,
    "Broken-Wing Put Diagonal": b_bwb_put_diag,
    "Diagonal Straddle": b_diag_straddle,
    "Rolling Diagonal": b_rolling_diag,
    "Ratio Diagonal": b_ratio_diag,
    "Short Call Diagonal": b_short_call_diag,
    "Short Straddle": b_short_straddle,
    "Short Strangle": b_short_strangle,
    "Strap": b_strap,
    "Strip": b_strip,
    "Iron Straddle": b_iron_straddle,
    "Reverse Ratio Straddle": b_rev_ratio_straddle,
    "Calendar Straddle": b_cal_straddle,
    "Diagonal Strangle": b_diag_strangle,
    "Iron Butterfly": b_iron_fly,
    "Broken-Wing Put Butterfly": b_bwb_put,
    "Wide-Wing Butterfly": b_wide_wing_fly,
    "Narrow-Wing Butterfly": b_narrow_wing_fly,
    "Double Butterfly": b_double_fly,
    "Reverse Butterfly": b_reverse_fly,
    "Calendar Butterfly": b_cal_fly,
    "Earnings Butterfly": b_earnings_fly,
    "Iron Condor": b_iron_condor,
    "Iron Condor Narrow": b_ic_narrow,
    "Iron Condor Wide": b_ic_wide,
    "Reverse Iron Condor": b_rev_ic,
    "Broken-Wing Iron Condor": b_bwb_ic,
    "Asymmetric Iron Condor": b_asym_ic,
    "Double Condor": b_double_condor,
    "Skewed Iron Condor": b_skewed_ic,
    "Delta-Neutral Iron Condor": b_dn_ic,
    "Earnings Iron Condor": b_earn_ic,
    "Zero-DTE Iron Condor": b_0dte_ic,
    "Zero-DTE Iron Butterfly": b_0dte_ifly,
    "Call Ratio Spread 1x2": b_call_ratio_1x2,
    "Put Ratio Spread 1x2": b_put_ratio_1x2,
    "Call Ratio Spread 1x3": b_call_ratio_1x3,
    "Put Ratio Spread 1x3": b_put_ratio_1x3,
    "Bullish Seagull": b_bull_seagull,
    "Bearish Seagull": b_bear_seagull,
    "Covered Ratio Spread Call": b_cov_ratio_call,
    "Zero-Cost Call Ratio Spread": b_zc_call_ratio,
    "Broken-Wing Call Ratio": b_bwb_call_ratio,
    "Broken-Wing Put Ratio": b_bwb_put_ratio,
    "Jade Lizard": b_jade_lizard,
    "Reverse Jade Lizard": b_rev_jade,
    "Big Lizard": b_big_lizard,
    "Twisted Sister": b_twisted_sister,
    "Iron Fly": b_iron_fly,
    "Reverse Iron Fly": b_rev_iron_fly,
    "Tail-Risk Hedge": b_tail_risk,
    "Crash Put Spread": b_crash_put_spread,
    "Earnings Long Straddle": b_earn_long_straddle,
    "Earnings Long Strangle": b_earn_long_strangle,
    "Earnings Butterfly Event": b_earnings_fly,
    "Earnings Iron Condor Event": b_earn_ic,
    "Zero-DTE Vertical": b_0dte_vertical,
    "Zero-DTE Butterfly Event": b_0dte_ifly,
    "Zero-DTE Iron Butterfly Event": b_0dte_ifly,
    "Zero-DTE Iron Condor Event": b_0dte_ic,
    "Weekly Strangle Event": b_weekly_strangle_event,
    "Overnight Gap Hedge": b_overnight_gap_hedge,
}

# Abstract / non-concrete catalog names (no fixed legs) — recorded as skipped
SKIPPED_ABSTRACT = [
    "Buffered-Protection Structure",
    "Defined-Outcome Structure",
    "Theta-Positive Spread",
    "Vega-Positive Structure",
    "Volatility Skew Trade",
    "Term-Structure Trade",
    "Variance Risk Premium Structure",
    "Pre-Event IV-Expansion Trade",
    "Post-Event IV-Crush Trade",
]


def slug(name: str) -> str:
    s = name.lower().replace("'", "").replace("/", "_")
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    while "__" in "".join(out):
        s2 = "".join(out).replace("__", "_")
        out = list(s2)
    return "".join(out).strip("_")[:80]


def package_value_ext(legs, d, spy: pd.DataFrame) -> Optional[float]:
    """Options via Polygon series; STK via SPY close * shares (no *100)."""
    total = 0.0
    for leg in legs:
        if leg.symbol == "STK":
            if d not in spy.index:
                px = spy["close"].asof(d) if hasattr(spy["close"], "asof") else None
                if px is None or (isinstance(px, float) and np.isnan(px)):
                    return None
            else:
                px = float(spy.loc[d, "close"])
            total += leg.qty * float(px)  # shares
        else:
            px = bt._px_on(leg.series, d)
            if px is None:
                return None
            total += leg.qty * px * 100.0
    return total


def run_strategy_ext(name, builder, spy, entry_dates, tp_pct, end, sl_pct=0.0):
    trades = []
    for d0 in entry_dates:
        if d0 not in spy.index:
            later = [x for x in spy.index if x >= d0]
            if not later:
                continue
            d0 = later[0]
        spot = float(spy.loc[d0, "close"])
        exp_near = bt.next_friday(d0, weeks_ahead=3)
        exp_far = bt.next_friday(d0, weeks_ahead=7)
        if exp_near > end + timedelta(days=7):
            continue
        raw = builder(spot, d0, exp_near, exp_far)
        unique = {}
        leg_pos = []
        ok = True
        for qty, right, strike, exp in raw:
            if right == "STK":
                # synthetic series unused; symbol marker
                leg_pos.append(bt.LegPos(qty=qty, symbol="STK", series=pd.Series(dtype=float)))
                continue
            sym = bt._occ(d0, strike, right, exp)
            if sym not in unique:
                unique[sym] = bt.fetch_option_daily(sym, d0, min(exp, end))
            ser = unique[sym]
            if bt._px_on(ser, d0) is None:
                ok = False
                break
            leg_pos.append(bt.LegPos(qty=qty, symbol=sym, series=ser))
        if not ok or not leg_pos:
            continue
        entry_val = package_value_ext(leg_pos, d0, spy)
        if entry_val is None:
            continue
        unit_cost = entry_val
        # Stock packages can be huge — risk gate on |options net| if stock present
        has_stk = any(lp.symbol == "STK" for lp in leg_pos)
        if has_stk:
            opt_only = [lp for lp in leg_pos if lp.symbol != "STK"]
            opt_val = package_value_ext(opt_only, d0, spy) if opt_only else 0.0
            if opt_val is None:
                continue
            # size by option risk / credit magnitude within RISK_USD
            basis = abs(opt_val) if abs(opt_val) >= 1.0 else abs(unit_cost)
            if basis < 1.0:
                continue
            if opt_val > 0 and opt_val > RISK_USD:
                continue
            mult = 1
            if opt_val > 0:
                mult = max(int(RISK_USD / opt_val), 1)
                while mult > 1 and opt_val * mult > RISK_USD:
                    mult -= 1
        else:
            if abs(unit_cost) < 1.0:
                continue
            if unit_cost > 0:
                if unit_cost > RISK_USD:
                    continue
                mult = max(int(RISK_USD / unit_cost), 1)
                while mult > 1 and unit_cost * mult > RISK_USD:
                    mult -= 1
            else:
                mult = 1
        for lp in leg_pos:
            lp.qty *= mult
        entry_val *= mult
        premium_basis = abs(entry_val) if not has_stk else abs(
            package_value_ext([lp for lp in leg_pos if lp.symbol != "STK"], d0, spy) or entry_val
        )
        if premium_basis < 1.0:
            premium_basis = abs(entry_val) if abs(entry_val) >= 1.0 else 1.0
        tp_dollars = None if tp_pct <= 0 else premium_basis * (tp_pct / 100.0)
        sl_dollars = None if sl_pct <= 0 else premium_basis * (sl_pct / 100.0)

        def _pnl(mark: float) -> float:
            return mark - entry_val

        hold_end = min(exp_near, end)
        sessions = [x for x in spy.index if d0 < x <= hold_end]
        exit_d = exit_val = exit_reason = pnl = None
        for d in sessions:
            mark = package_value_ext(leg_pos, d, spy)
            if mark is None:
                continue
            pnl_now = _pnl(mark)
            if sl_dollars is not None and pnl_now <= -sl_dollars:
                exit_d, exit_val, exit_reason, pnl = d, mark, f"SL_{int(sl_pct)}PCT", pnl_now
                break
            if tp_dollars is not None and pnl_now >= tp_dollars:
                exit_d, exit_val, exit_reason, pnl = d, mark, f"TP_{tp_pct}PCT", pnl_now
                break
        if exit_d is None:
            for d in reversed(sessions):
                mark = package_value_ext(leg_pos, d, spy)
                if mark is None:
                    continue
                pnl = _pnl(mark)
                exit_d, exit_val, exit_reason = d, mark, "EXPIRY_FLATTEN"
                break
            if exit_d is None:
                continue
        trades.append({
            "strategy": name,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "entry_date": d0.isoformat(),
            "exit_date": exit_d.isoformat(),
            "spot": round(spot, 2),
            "exp_near": exp_near.isoformat(),
            "mult": mult,
            "entry_val": round(entry_val, 2),
            "exit_val": round(exit_val, 2),
            "premium_basis": round(float(premium_basis), 2),
            "pnl": round(float(pnl), 2),
            "exit_reason": exit_reason,
            "legs": [f"{lp.qty}x{lp.symbol}" for lp in leg_pos],
        })
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--tp", default=",".join(str(x) for x in TP_PCTS))
    ap.add_argument("--max-entries", type=int, default=0)
    ap.add_argument("--strategies", default="all")
    args = ap.parse_args()

    if not (os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY")):
        raise SystemExit("POLYGON_API_KEY not set")

    tp_list = [int(x) for x in args.tp.split(",") if x.strip()]
    end = date.today()
    start = end - timedelta(days=int(args.years * 365.25))
    print(f"[catalog-bt] SPY untested catalog {start}→{end} risk=${RISK_USD} TPs={tp_list}")
    print(f"[catalog-bt] concrete strategies={len(STRATEGIES)} skipped_abstract={len(SKIPPED_ABSTRACT)}")

    spy = bt.fetch_spy_daily(start, end)
    entries = bt.mondays_between(start, end)
    entries = [d for d in entries if d in spy.index or any(x >= d for x in spy.index)]
    if args.max_entries:
        entries = entries[: args.max_entries]
    print(f"[catalog-bt] entry Mondays: {len(entries)}")

    if args.strategies == "all":
        items = list(STRATEGIES.items())
    else:
        want = set(args.strategies.split("|"))
        items = [(k, v) for k, v in STRATEGIES.items() if k in want]

    ranking = []
    root = archive_root()
    for sname, builder in items:
        for tp in tp_list:
            label = f"{slug(sname)}__tp{tp}"
            print(f"\n=== {label} ===", flush=True)
            t0 = time.time()
            trades = run_strategy_ext(sname, builder, spy, entries, tp, end, sl_pct=0.0)
            summary = bt.summarize(trades)
            print(
                f"  trades={summary['trades']} pnl=${summary['total_pnl']} "
                f"wr={summary['win_rate']} ({time.time()-t0:.1f}s)",
                flush=True,
            )
            payload = {
                "strategy": sname,
                "tp_pct": tp,
                "sl_pct": 0,
                "no_stop_loss": True,
                "risk_usd": RISK_USD,
                "window": {"start": start.isoformat(), "end": end.isoformat()},
                "summary": summary,
                "trades": trades,
            }
            out = root / f"{label}.json"
            out.write_text(json.dumps(payload, indent=2, default=str))
            ranking.append({
                "label": label,
                "strategy": sname,
                "tp_pct": tp,
                **summary,
                "path": str(out),
            })
            # progress checkpoint
            (root / "RANKING_PARTIAL.json").write_text(json.dumps({
                "n": len(ranking),
                "ranked_best_first": sorted(
                    ranking, key=lambda r: (r.get("total_pnl") is None, -(r.get("total_pnl") or 0))
                )[:50],
            }, indent=2))

    ranking_sorted = sorted(
        ranking, key=lambda r: (r.get("total_pnl") is None, -(r.get("total_pnl") or 0))
    )
    best = {}
    for r in ranking_sorted:
        if r["strategy"] not in best:
            best[r["strategy"]] = r
    rank_path = root / f"RANKING_NOSTOP_TPGRID_{end.isoformat()}.json"
    rank_path.write_text(json.dumps({
        "rules": {
            "underlying": "SPY",
            "risk_usd": RISK_USD,
            "entry": "weekly Monday",
            "exit": f"TP grid {tp_list}% — NO STOP; else flatten near expiry",
            "pricing": "Polygon daily option aggregates (+ SPY shares for stock+opt)",
            "already_tested_excluded": sorted(ALREADY_TESTED),
            "skipped_abstract": SKIPPED_ABSTRACT,
            "strategies_n": len(items),
        },
        "ranked_best_first": ranking_sorted,
        "winner": ranking_sorted[0] if ranking_sorted else None,
        "best_tp_per_strategy": best,
    }, indent=2))
    print(f"[catalog-bt] wrote {rank_path}")
    with (root / "RUN_INDEX.jsonl").open("a") as f:
        f.write(json.dumps({"ts": date.today().isoformat(), "path": str(rank_path), "n": len(ranking)}) + "\n")
    print("[catalog-bt] DONE")


if __name__ == "__main__":
    main()
