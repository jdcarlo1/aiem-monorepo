"""
regime_macro_patch.py
====================================================================
Extends regime_detector.py with a macro-event overlay. If today is a
high-impact economic calendar day (FOMC, CPI, NFP), this forces extra
defensiveness on top of whatever the price-based regime classifier
already says — because volatility/trend data is backward-looking and
won't have reacted to an event that hasn't happened yet TODAY.
====================================================================
"""

import datetime as dt
from typing import Dict, Any

from regime_detector import get_current_regime, REGIME_SIGNAL_MULTIPLIERS
from economic_calendar import is_high_impact_day


def get_regime_with_macro_overlay(db_url: str, proxy_ticker: str = "SPY") -> Dict[str, Any]:
    """
    Combines the price-based regime classification with today's macro
    calendar. If a high-impact event is scheduled today, multipliers are
    additionally dampened regardless of what price action currently shows
    — because price hasn't reacted to the event yet.
    """
    base_regime = get_current_regime(db_url, proxy_ticker)
    macro = is_high_impact_day(db_url)

    multipliers = dict(base_regime.get("multipliers", {}))
    if macro["high_impact_day"]:
        # Additional dampening on top of the base regime's multipliers
        multipliers["confidence_multiplier"] = multipliers.get("confidence_multiplier", 1.0) * 0.5
        multipliers["position_size_multiplier"] = multipliers.get("position_size_multiplier", 1.0) * 0.5
        multipliers["exit_sensitivity"] = multipliers.get("exit_sensitivity", 1.0) * 1.3

    return {
        "base_regime": base_regime.get("regime"),
        "high_impact_macro_day": macro["high_impact_day"],
        "macro_events_today": macro["events_today"],
        "final_multipliers": multipliers,
        "checked_at": dt.datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        print(get_regime_with_macro_overlay(db_url))
    else:
        print("Set DATABASE_URL to test against real data.")
