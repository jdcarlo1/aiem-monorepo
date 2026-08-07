"""
Regression: 2026-08-04 options pipeline crash —
TypeError: '>' not supported between instances of 'NoneType' and 'float'
when pattern_result['pattern_score'] was explicitly None.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _coerce_pattern_score(pattern_result: dict) -> float:
    """Mirror the fix in aiem_options_scheduler._execute_job Stage PAT."""
    _ps_raw = pattern_result.get("pattern_score")
    return 0.5 if _ps_raw is None else float(_ps_raw)


def test_none_pattern_score_coerces_to_neutral():
    score = _coerce_pattern_score({"pattern_score": None, "all_patterns": []})
    assert score == 0.5
    # Must not TypeError:
    label = (
        "BULLISH" if score > 0.6 else "BEARISH" if score < 0.4 else "NEUTRAL"
    )
    assert label == "NEUTRAL"


def test_missing_key_uses_neutral_default():
    # .get without fix would use 0.5; with explicit None-guard same outcome
    score = _coerce_pattern_score({})
    assert score == 0.5


def test_numeric_pattern_score_preserved():
    assert abs(_coerce_pattern_score({"pattern_score": 0.72}) - 0.72) < 1e-9


def test_compute_req6_score_accepts_none_iv_rank():
    import aiem_options_pipeline as pipe

    contract = {
        "probability_estimate": 0.4,
        "expected_return": 0.5,
        "premium_at_risk": 250,
        "profit_target": 400,
        "volume": 1000,
        "open_interest": 2000,
        "slippage_pct": 0.05,
        "theta": 0.02,
        "bid": 1.0,
        "ask": 1.2,
        "dte": 10,
    }
    stock = {
        "stock_direction": "BULLISH",
        "market_regime": "TRENDING",
        "vwap_position": "ABOVE",
        "close_strength": 0.6,
        "iv_crush_risk": "",
        "pc_skew_tag": "",
    }
    result = pipe.compute_req6_score(contract, "CALL", stock, None, {})
    assert isinstance(result["score"], (int, float))
    assert 0 <= result["score"] <= 100


if __name__ == "__main__":
    test_none_pattern_score_coerces_to_neutral()
    test_missing_key_uses_neutral_default()
    test_numeric_pattern_score_preserved()
    test_compute_req6_score_accepts_none_iv_rank()
    print("ALL_PASS")
