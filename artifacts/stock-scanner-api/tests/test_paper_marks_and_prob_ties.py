"""Unit tests for paper mark refresh + prob-engine identical-score tie-break."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# daily_picks imports top-level `config.DB_URL` — stub for unit tests.
if "config" not in sys.modules:
    _cfg = types.ModuleType("config")
    _cfg.DB_URL = "postgresql://unit-test"
    sys.modules["config"] = _cfg


def _compute(trade_type, direction, entry_f, qty_f, not_f, underlying_last,
             option_mid=None, entry_mid=None):
    """Inline copy of mark math (avoids importing Flask main.py)."""
    def _premium_candidate(value, underlying_entry) -> bool:
        try:
            v = float(value)
            u = float(underlying_entry or 0)
        except Exception:
            return False
        if v <= 0:
            return False
        return v < max(1.0, u * 0.35)

    ttype = (trade_type or "STOCK").upper()
    direction = (direction or "BULLISH").upper()
    entry_f = float(entry_f or 0)
    qty_f = float(qty_f or 0)
    not_f = float(not_f or 0)

    if ttype in ("CALL_OPTION", "PUT_OPTION"):
        if (option_mid and entry_mid and _premium_candidate(entry_mid, entry_f)):
            last = float(option_mid)
            em = float(entry_mid)
            contracts = max(1.0, not_f / (em * 100.0)) if em > 0 else 1.0
            pnl = round((last - em) * contracts * 100.0, 2)
            pnl_pct = round((last - em) / em * 100.0, 4) if em > 0 else 0.0
            return last, pnl, pnl_pct, "option_mid", False

    last = float(underlying_last or 0)
    if last <= 0 or entry_f <= 0:
        if ttype in ("CALL_OPTION", "PUT_OPTION") and option_mid and float(option_mid) > 0:
            return float(option_mid), None, None, "option_mid_no_underlying", True
        return None, None, None, "unavailable", True

    if ttype == "CALL_OPTION":
        move = (last - entry_f) / entry_f * 100.0
        pnl_pct = round(max(-100.0, move * 2.0), 4)
        pnl = round(not_f * pnl_pct / 100.0, 2)
        if option_mid and float(option_mid) > 0:
            return float(option_mid), pnl, pnl_pct, "option_last_synthetic_pnl", True
        return last, pnl, pnl_pct, "synthetic_underlying_2x", True
    if ttype == "PUT_OPTION":
        move = (entry_f - last) / entry_f * 100.0
        pnl_pct = round(max(-100.0, move * 2.0), 4)
        pnl = round(not_f * pnl_pct / 100.0, 2)
        if option_mid and float(option_mid) > 0:
            return float(option_mid), pnl, pnl_pct, "option_last_synthetic_pnl", True
        return last, pnl, pnl_pct, "synthetic_underlying_2x", True
    if ttype == "SHORT_STOCK" or direction == "BEARISH":
        pnl = round((entry_f - last) * qty_f, 2)
        pnl_pct = round((entry_f - last) / entry_f * 100.0, 4)
        return last, pnl, pnl_pct, "stock_quote", False
    pnl = round((last - entry_f) * qty_f, 2)
    pnl_pct = round((last - entry_f) / entry_f * 100.0, 4)
    return last, pnl, pnl_pct, "stock_quote", False


def test_identical_prob_vectors_get_polygon_tiebreak():
    from aiem_probability_engine.daily_picks import _score_and_rank
    import aiem_probability_engine.daily_picks as dp

    rows = [
        {"ticker": "AAPL", "prob_up_1d": 0.78, "prob_up_2d": 0.84, "prob_up_3d": 0.86,
         "prob_up_4d": 0.56, "confidence": 0.32, "regime_tag": "full_exposure",
         "edge_after_cost_prob_pts": 28.5, "warnings_json": []},
        {"ticker": "NVDA", "prob_up_1d": 0.78, "prob_up_2d": 0.84, "prob_up_3d": 0.86,
         "prob_up_4d": 0.56, "confidence": 0.32, "regime_tag": "full_exposure",
         "edge_after_cost_prob_pts": 28.5, "warnings_json": []},
        {"ticker": "PLTR", "prob_up_1d": 0.78, "prob_up_2d": 0.84, "prob_up_3d": 0.86,
         "prob_up_4d": 0.56, "confidence": 0.32, "regime_tag": "full_exposure",
         "edge_after_cost_prob_pts": 28.5, "warnings_json": []},
    ]
    dp._polygon_diff_scores = lambda tickers: {"AAPL": 0.01, "NVDA": 0.04, "PLTR": 0.02}
    top = _score_and_rank(rows, 3)
    assert [r["ticker"] for r in top] == ["NVDA", "PLTR", "AAPL"]
    assert top[0]["_score"] > top[1]["_score"] > top[2]["_score"]
    assert any("identical_prob_vector" in str(w) for w in (top[0].get("warnings_json") or []))


def test_compute_mark_stock_and_option_paths():
    last, pnl, pct, src, syn = _compute("STOCK", "BULLISH", 100, 10, 1000, 110)
    assert last == 110 and pnl == 100 and abs(pct - 10) < 1e-6 and src == "stock_quote" and syn is False

    last, pnl, pct, src, syn = _compute("CALL_OPTION", "BULLISH", 100, 10, 1000, 110)
    assert last == 110 and src == "synthetic_underlying_2x" and syn is True
    assert abs(pct - 20.0) < 1e-6
    assert abs(pnl - 200.0) < 1e-6

    # Real option premium MTM when entry mid looks like a premium
    last, pnl, pct, src, syn = _compute(
        "CALL_OPTION", "BULLISH", 100, 1, 500, 100, option_mid=2.5, entry_mid=2.0
    )
    assert src == "option_mid" and syn is False
    assert last == 2.5
    assert abs(pct - 25.0) < 1e-6

    # Live option last + synthetic pnl when entry mid was underlying (legacy rows)
    last, pnl, pct, src, syn = _compute(
        "CALL_OPTION", "BULLISH", 100, 10, 1000, 110, option_mid=3.25, entry_mid=100.0
    )
    assert last == 3.25
    assert src == "option_last_synthetic_pnl" and syn is True
    assert abs(pct - 20.0) < 1e-6

    # Null underlying still surfaces option last for UI
    last, pnl, pct, src, syn = _compute(
        "CALL_OPTION", "BULLISH", 100, 10, 1000, 0, option_mid=1.5, entry_mid=None
    )
    assert last == 1.5 and src == "option_mid_no_underlying" and pnl is None
