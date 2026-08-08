"""Unit tests for autonomous OE paper fill / P&L helpers."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiem_options_paper_fill import (
    paper_buy_fill,
    paper_realized_pnl,
    paper_slippage_dollars,
)


def test_paper_buy_fill_uses_ask():
    px, q = paper_buy_fill(1.20, 1.40)
    assert px == 1.40
    assert q == "ASK"


def test_paper_buy_fill_one_sided():
    px, q = paper_buy_fill(0, 1.50, one_sided=True)
    assert px == 1.50
    assert q == "ONE_SIDED_ASK"


def test_paper_buy_fill_missing_ask():
    px, q = paper_buy_fill(1.2, None)
    assert px is None
    assert q == "NO_ASK"


def test_slippage_half_spread_dollars():
    # (1.40-1.20)/2 = 0.10 → $10 per contract
    assert paper_slippage_dollars(1.20, 1.40, quantity=1) == 10.0
    assert paper_slippage_dollars(1.20, 1.40, quantity=2) == 20.0


def test_slippage_one_sided():
    # half of ask * 100
    assert paper_slippage_dollars(0, 2.00, quantity=1) == 100.0


def test_realized_pnl_long_winner():
    # bought 1.40, expired intrinsic 2.00 → +$60 - fees - slip
    pnl, ret = paper_realized_pnl(
        entry_price=1.40, exit_price=2.00, quantity=1, fees_est=0.65, slippage_est=10.0
    )
    assert pnl == round(60.0 - 0.65 - 10.0, 4)
    assert ret == round((2.0 - 1.4) / 1.4, 6)


def test_realized_pnl_worthless():
    pnl, ret = paper_realized_pnl(
        entry_price=1.40, exit_price=0.0, quantity=1, fees_est=0.65, slippage_est=10.0
    )
    assert pnl == round(-140.0 - 0.65 - 10.0, 4)
    assert ret == round(-1.0, 6)
