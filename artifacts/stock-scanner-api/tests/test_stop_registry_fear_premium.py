"""
Regression: 2026-08-04 zero-picks — fear_premium_gex / gap_down_distribution
must have thesis stops, and unknown sources must stay fail-closed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aiem_position_sizing as ps  # noqa: E402


def test_fear_premium_gex_stop_defined():
    stop = ps.derive_stop("fear_premium_gex", {"entry_price": 85.9577})
    assert stop["defined"] is True
    assert stop["stop_price"] < 85.9577
    assert "fear_premium_gex" in stop["stop_basis"]


def test_gap_down_distribution_stop_defined():
    stop = ps.derive_stop("gap_down_distribution", {"entry_price": 50.0})
    assert stop["defined"] is True


def test_unknown_source_still_fail_closed():
    stop = ps.derive_stop("not_a_real_source", {"entry_price": 10.0})
    assert stop["defined"] is False
    assert stop["stop_basis"] == "NO_INVALIDATION_POINT_DEFINED_FOR_SOURCE"


def test_missing_entry_still_undefined():
    stop = ps.derive_stop("fear_premium_gex", {"entry_price": 0})
    assert stop["defined"] is False
    assert "MISSING_ENTRY_PRICE" in stop["stop_basis"]


if __name__ == "__main__":
    test_fear_premium_gex_stop_defined()
    test_gap_down_distribution_stop_defined()
    test_unknown_source_still_fail_closed()
    test_missing_entry_still_undefined()
    print("ALL_PASS")
