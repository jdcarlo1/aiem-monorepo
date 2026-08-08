"""Unit tests for paper fill realism fixes (ask entry / bid exit / dual slip / fees)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiem_options_paper_fill import (
    paper_buy_fill,
    paper_sell_fill,
    paper_slippage_dollars,
    paper_realized_pnl,
    paper_round_trip_fees,
)


def test_sell_fill_at_bid():
    px, q = paper_sell_fill(1.90, 2.10)
    assert px == 1.90
    assert q == "BID"


def test_sell_fill_one_sided():
    px, q = paper_sell_fill(None, 2.00)
    assert q == "ONE_SIDED_BID"
    assert px == round(2.00 * 0.85, 4)


def test_condor_fees():
    assert paper_round_trip_fees(n_legs=4, quantity=1) == 5.20


def test_asymmetry_ask_in_bid_out():
    entry, _ = paper_buy_fill(1.20, 1.40)
    exit_px, _ = paper_sell_fill(1.90, 2.10)
    entry_slip = paper_slippage_dollars(1.20, 1.40, 1)
    exit_slip = paper_slippage_dollars(1.90, 2.10, 1)
    total_slip = entry_slip + exit_slip
    pnl, _ = paper_realized_pnl(
        entry_price=entry,
        exit_price=exit_px,
        quantity=1,
        fees_est=0.65,
        slippage_est=total_slip,
    )
    # Prior demo: exit-at-bid with entry slip only ≈ 39.35; with dual slip lower.
    assert entry == 1.4
    assert exit_px == 1.9
    assert entry_slip == 10.0
    assert exit_slip == 10.0
    assert pnl == round((1.9 - 1.4) * 100 - 0.65 - 20.0, 4)


def test_slippage_always_subtracted():
    pnl, _ = paper_realized_pnl(
        entry_price=1.4, exit_price=1.9, fees_est=0, slippage_est=10
    )
    assert pnl == 40.0  # 50 - 10
