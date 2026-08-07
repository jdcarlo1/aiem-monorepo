"""
Bullish CALL seed + scoring metrics (2026-08-04).

Prior seed ranked only by pc_skew_pp DESC → exclusively FEAR_PREMIUM puts.
Up-day names (ENTG/IDXX/AVGO/…) never entered the options queue.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_seed_lane_limits_call_bias():
    from aiem_options_scheduler import _seed_lane_limits

    assert _seed_lane_limits(15) == (10, 5)
    assert _seed_lane_limits(8) == (5, 3)
    assert _seed_lane_limits(6) == (4, 2)
    assert _seed_lane_limits(2) == (1, 1)


def test_merge_seed_lanes_call_first_dedupes():
    from aiem_options_scheduler import _merge_seed_lanes

    call = [("ENTG", None, -2.0, "SHORT_GAMMA", "NORMAL", "CALL"),
            ("AVGO", None, -5.0, "SHORT_GAMMA", "CALL_SKEW", "CALL")]
    put = [("INBK", None, 200.0, "NEAR_FLIP", "FEAR_PREMIUM", "PUT"),
           ("ENTG", None, 10.0, "NEAR_FLIP", "FEAR_PREMIUM", "PUT")]  # dupe
    merged = _merge_seed_lanes(call, put)
    assert [r[0] for r in merged] == ["ENTG", "AVGO", "INBK"]
    assert merged[0][5] == "CALL"


def test_call_skew_d11_parity_with_fear_premium():
    import aiem_options_pipeline as pipe

    contract = {
        "probability_estimate": 0.42,
        "expected_return": 0.65,
        "premium_at_risk": 250,
        "profit_target": 400,
        "volume": 2500,
        "open_interest": 8000,
        "slippage_pct": 0.04,
        "theta": 0.025,
        "bid": 1.2,
        "ask": 1.35,
        "dte": 12,
    }
    call_stock = {
        "stock_direction": "BULL",
        "market_regime": "SHORT_GAMMA_TRENDING",
        "vwap_position": "ABOVE_VWAP",
        "close_strength": 0.85,
        "iv_crush_risk": "LOW",
        "pc_skew_tag": "CALL_SKEW",
        "morning_momentum_pct": 0.03,
    }
    put_stock = {
        "stock_direction": "BEAR",
        "market_regime": "LONG_GAMMA_FEAR_PREMIUM",
        "vwap_position": "BELOW_VWAP",
        "close_strength": 0.25,
        "iv_crush_risk": "LOW",
        "pc_skew_tag": "FEAR_PREMIUM",
        "morning_momentum_pct": -0.01,
    }
    call = pipe.compute_req6_score(contract, "CALL", call_stock, 0.4, {})
    put = pipe.compute_req6_score(contract, "PUT", put_stock, 0.4, {})
    # D11 skew bonus parity: both get +25 → base 85 before other adj
    assert call["component_scores"]["D11_options_flow_confirmation"] == 85
    assert put["component_scores"]["D11_options_flow_confirmation"] == 85
    assert call["score"] >= 55


def test_morning_momentum_boosts_call_d10():
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
    base = {
        "stock_direction": "BULL",
        "market_regime": "NEUTRAL",
        "vwap_position": "ABOVE_VWAP",
        "close_strength": 0.6,
        "iv_crush_risk": "LOW",
        "pc_skew_tag": "NORMAL",
        "morning_momentum_pct": 0.0,
    }
    rip = dict(base, morning_momentum_pct=0.05)
    s0 = pipe.compute_req6_score(contract, "CALL", base, 0.4, {})
    s1 = pipe.compute_req6_score(contract, "CALL", rip, 0.4, {})
    assert s1["component_scores"]["D10_technical_confirmation"] > \
        s0["component_scores"]["D10_technical_confirmation"]
    assert s1["score"] > s0["score"]


if __name__ == "__main__":
    test_seed_lane_limits_call_bias()
    test_merge_seed_lanes_call_first_dedupes()
    test_call_skew_d11_parity_with_fear_premium()
    test_morning_momentum_boosts_call_d10()
    print("ALL_PASS")
