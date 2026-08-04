"""Pre-move washout / continuation / thrust — wide-net cohort regression."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_score_washout_in_conviction_band():
    from aiem_pre_move_signals import score_washout_setup

    s = score_washout_setup(
        d2_pct=-18.0, close_strength=0.05, rvol=1.5, range_pct=14.0, dvol=5e9
    )
    assert 5.0 <= s <= 9.0


def test_jul29_wide_net_catches_cohort():
    """Wide net must include liquid + small-cap washouts AND ORCL thrust."""
    import psycopg2
    from aiem_pre_move_signals import (
        scan_washout_reclaim, scan_thrust_pullback, scan_all_pre_move,
    )

    db = os.environ.get("DATABASE_URL")
    if not db:
        print("SKIP no DATABASE_URL")
        return

    washout_cohort = {
        "AEHR", "AXTI", "NBIS", "IREN", "MU", "AMAT", "LRCX", "ENTG",
        "VSH", "PENG", "VPG", "LWLG", "MRAM", "WOLF", "LUNR",
    }
    with psycopg2.connect(db, connect_timeout=8) as conn, conn.cursor() as cur:
        rows = scan_washout_reclaim(cur, asof=date(2026, 7, 29), limit=500)
        thrust = scan_thrust_pullback(cur, asof=date(2026, 7, 29), limit=40)
        all_pm = scan_all_pre_move(cur, asof=date(2026, 7, 29))

    tickers = {r["ticker"] for r in rows}
    hit = washout_cohort & tickers
    # Wide net: most of the named washout cohort must appear
    assert len(hit) >= 10, f"wide-net recall too low: hit={sorted(hit)} missing={sorted(washout_cohort-tickers)}"
    assert "LWLG" in tickers, "LWLG must be in washout universe"
    assert "MRAM" in tickers, "MRAM must be in washout universe"
    assert "ORCL" in {r["ticker"] for r in thrust}, "ORCL must be thrust_pullback"
    assert "ORCL" in {r["ticker"] for r in all_pm}


def test_seed_lane_limits_call_heavy():
    from aiem_options_scheduler import _seed_lane_limits

    assert _seed_lane_limits(15) == (10, 5)
    assert _seed_lane_limits(8) == (5, 3)
    assert _seed_lane_limits(6) == (4, 2)


def test_stops_registered():
    from aiem_position_sizing import _STOP_REGISTRY

    assert "washout_reclaim" in _STOP_REGISTRY
    assert "momentum_continuation" in _STOP_REGISTRY
    assert "thrust_pullback" in _STOP_REGISTRY


if __name__ == "__main__":
    test_score_washout_in_conviction_band()
    test_seed_lane_limits_call_heavy()
    test_stops_registered()
    test_jul29_wide_net_catches_cohort()
    print("ALL_PASS")
