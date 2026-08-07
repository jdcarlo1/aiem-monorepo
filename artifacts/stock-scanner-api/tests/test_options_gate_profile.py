"""Unit tests for OE gate profile + single-leg verify readiness."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# intel imports psycopg2 at module load; unit tests don't need a live driver.
if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

from aiem_options_gate_profile import resolve_gate_profile, describe_gate_profile
from aiem_options_intel import verify_options_decision_inputs


def _stock_fields(**overrides):
    base = {
        "stock_direction": "BULLISH",
        "market_regime": "TRENDING",
        "iv_rank": 0.4,
        "iv_crush_risk": "LOW",
        "vwap_position": "ABOVE",
        "sector_strength": "STRONG",
        "market_breadth": "POSITIVE",
    }
    base.update(overrides)
    return base


def _leg(**overrides):
    base = {
        "delta": 0.35,
        "gamma": 0.02,
        "theta": -0.04,
        "vega": 0.12,
        "iv": 0.45,
        "volume": None,          # optional — Tradier may be absent
        "open_interest": None,
        "bid": 1.20,
        "ask": 1.40,
        "bid_ask_spread_pct": 0.15,
        "breakeven": 52.0,
        "premium_at_risk": 130.0,
        "expected_move": 2.5,
        "probability_estimate": 0.38,
        "expected_return": 0.55,
        "dte": 9,
        "slippage_pct": 0.08,
    }
    base.update(overrides)
    return {**_stock_fields(), **base}


@pytest.fixture(autouse=True)
def _clear_gate_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("OE_GATE"):
            monkeypatch.delenv(k, raising=False)
    yield


def test_default_profile_is_balanced():
    cfg = resolve_gate_profile()
    assert cfg["profile"] == "balanced"
    assert cfg["allow_single_leg"] is True
    assert cfg["allow_one_sided_quotes"] is True
    assert cfg["score_min"] == 50.0
    assert cfg["min_oi"] == 250
    assert "profile=balanced" in describe_gate_profile(cfg)


def test_strict_profile_restores_legacy_thresholds(monkeypatch):
    monkeypatch.setenv("OE_GATE_PROFILE", "strict")
    cfg = resolve_gate_profile()
    assert cfg["min_oi"] == 500
    assert cfg["min_volume"] == 100
    assert cfg["max_spread_pct"] == 0.20
    assert cfg["score_min"] == 55.0
    assert cfg["margin_min"] == 10.0
    assert cfg["allow_single_leg"] is False


def test_env_override_beats_profile(monkeypatch):
    monkeypatch.setenv("OE_GATE_PROFILE", "strict")
    monkeypatch.setenv("OE_GATE_MIN_OI", "123")
    cfg = resolve_gate_profile()
    assert cfg["min_oi"] == 123
    assert cfg["score_min"] == 55.0  # untouched


def test_single_leg_call_ready_when_put_absent(monkeypatch):
    monkeypatch.setenv("OE_GATE_PROFILE", "balanced")
    call = _leg()
    put = _stock_fields()  # no bid/ask → inactive
    result = verify_options_decision_inputs("MOVER", call, put)
    assert result["ready_for_decision"] is True
    assert result["call_eligible"] is True
    assert result["put_eligible"] is False
    assert result["gate_profile"] == "balanced"


def test_strict_still_requires_both_legs(monkeypatch):
    monkeypatch.setenv("OE_GATE_PROFILE", "strict")
    call = _leg()
    put = _stock_fields()
    result = verify_options_decision_inputs("MOVER", call, put)
    assert result["ready_for_decision"] is False
    assert any(m.startswith("put:") for m in result["missing_fields"])


def test_oi_gate_fires_only_when_populated(monkeypatch):
    monkeypatch.setenv("OE_GATE_PROFILE", "balanced")
    call = _leg(open_interest=50, volume=10)  # below balanced floors
    put = _leg(delta=-0.35, probability_estimate=0.38)
    result = verify_options_decision_inputs("THIN", call, put)
    assert result["call_eligible"] is False
    assert any("OI <" in g for g in result["gate_failures"])


def test_none_oi_does_not_block_readiness(monkeypatch):
    monkeypatch.setenv("OE_GATE_PROFILE", "balanced")
    call = _leg(open_interest=None, volume=None)
    put = _leg(delta=-0.35)
    result = verify_options_decision_inputs("OK", call, put)
    assert result["ready_for_decision"] is True
    assert result["call_eligible"] is True
    assert result["put_eligible"] is True


def test_spread_gate_uses_profile_threshold(monkeypatch):
    monkeypatch.setenv("OE_GATE_PROFILE", "balanced")
    # 0.25 fails strict(0.20) but passes balanced(0.28)
    call = _leg(bid_ask_spread_pct=0.25, slippage_pct=0.12)
    put = _leg(delta=-0.35, bid_ask_spread_pct=0.25, slippage_pct=0.12)
    result = verify_options_decision_inputs("WIDE", call, put)
    assert result["ready_for_decision"] is True

    monkeypatch.setenv("OE_GATE_PROFILE", "strict")
    result2 = verify_options_decision_inputs("WIDE", call, put)
    assert result2["ready_for_decision"] is False
    assert any("spread" in g.lower() for g in result2["gate_failures"])
