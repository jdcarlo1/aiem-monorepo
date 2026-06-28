"""
fred_macro.py
---------------------------
Macro indicators sourced from FRED (Federal Reserve Economic Data) —
free, no API key required for the CSV endpoint used here. Extends
macro_cross_asset.py with two indicators that are more respected and
better-documented than the ^TNX/UUP proxies used there: the yield curve
shape and credit spreads.

WHY THESE TWO SPECIFICALLY
----------------------------
1. YIELD CURVE (10Y - 2Y spread): an inverted yield curve (2Y yielding
   more than 10Y) has preceded every U.S. recession in the post-war era
   with only one false positive — this is about as well-documented a
   macro indicator as exists in mainstream economics, not a fringe
   technical signal. It's also slow-moving (changes over weeks/months,
   not days), so it's meant as a standing backdrop check, not a day-to-day
   trading trigger.

2. CREDIT SPREADS (high-yield bond spread over Treasuries): credit
   markets often move before equity markets because bondholders get hurt
   by the same risk that eventually hits stock prices, and bond investors
   are typically more risk-averse / quicker to react. Widening spreads
   are a risk-off signal independent of what equity indices are doing.

Both are FREE, well-established, not proprietary indicators — this is the
opposite of the satellite/credit-card-panel vendor data discussed earlier;
no contract, no cost, just a public data series.

DATA SOURCE
-----------
Uses FRED's no-key-required CSV download endpoint:
  https://fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES_ID}
This works without registering for an API key. If you want the full FRED
JSON API later (more series, more metadata), sign up for a free key at
https://fred.stlouisfed.org/docs/api/api_key.html — instant, no cost,
no vendor relationship, just registration.

INTEGRATION
-----------
Same vote contract as macro_cross_asset.py and market_regime_overlay.py's
other indicators: {"vote": -1/0/1, "reason": str}.
from fred_macro import get_fred_macro_votes
votes.extend(get_fred_macro_votes())
"""

import io
import datetime as dt
from typing import Dict, Any, List, Optional

import pandas as pd
import requests

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# Series IDs used here:
#   T10Y2Y       - 10-Year Treasury minus 2-Year Treasury yield spread (percentage points)
#   BAMLH0A0HYM2 - ICE BofA US High Yield Index Option-Adjusted Spread (credit spread, percentage points)


def fetch_fred_series(series_id: str) -> Optional[pd.Series]:
    """
    Fetches a FRED series via the public CSV endpoint (no API key needed).
    Returns a pandas Series indexed by date, most recent value last.
    """
    url = FRED_CSV_URL.format(series_id=series_id)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["value"] != "."]  # FRED uses "." for missing observations
        df["value"] = df["value"].astype(float)
        return df.set_index("date")["value"]
    except Exception as e:
        print(f"[fred_macro] failed to fetch FRED series {series_id}: {e}")
        return None


def yield_curve_indicator(lookback_days: int = 60) -> Dict[str, Any]:
    """
    Votes risk-off if the 10Y-2Y spread is inverted (negative) AND has
    been inverted for a sustained period (not just a one-day noisy dip
    below zero) — sustained inversion is the historically meaningful
    pattern, a single day crossing zero is not. Votes risk-on if the
    curve is positively sloped and steepening (historically associated
    with healthy expansion, particularly coming out of a prior inversion).
    """
    series = fetch_fred_series("T10Y2Y")
    if series is None or len(series) < lookback_days:
        return {"vote": 0, "reason": "insufficient yield curve (T10Y2Y) data"}

    recent = series.iloc[-lookback_days:]
    current = float(series.iloc[-1])
    pct_days_inverted = float((recent < 0).mean())
    trend_20d = float(series.iloc[-1] - series.iloc[-20]) if len(series) >= 20 else 0.0

    if current < 0 and pct_days_inverted > 0.7:
        return {
            "vote": -1,
            "reason": f"Yield curve inverted ({current:.2f}, {pct_days_inverted:.0%} of last "
                      f"{lookback_days}d) — historically the single most reliable recession-risk "
                      f"indicator; this is a standing macro caution, not a day-trade signal",
        }
    if current > 0 and trend_20d > 0.10:
        return {
            "vote": 1,
            "reason": f"Yield curve positively sloped ({current:.2f}) and steepening "
                      f"(+{trend_20d:.2f} over 20d) — consistent with healthy expansion",
        }
    return {"vote": 0, "reason": f"Yield curve neutral/mixed (current={current:.2f}, "
                                   f"{pct_days_inverted:.0%} inverted over {lookback_days}d)"}


def credit_spread_indicator(lookback_days: int = 60) -> Dict[str, Any]:
    """
    Votes risk-off if high-yield credit spreads are both elevated relative
    to their recent average AND widening — credit markets pricing in more
    risk than equity markets may currently reflect. Votes risk-on if
    spreads are tight and stable/narrowing.
    """
    series = fetch_fred_series("BAMLH0A0HYM2")
    if series is None or len(series) < lookback_days:
        return {"vote": 0, "reason": "insufficient credit spread (BAMLH0A0HYM2) data"}

    recent = series.iloc[-lookback_days:]
    current = float(series.iloc[-1])
    avg = float(recent.mean())
    change_10d = float(series.iloc[-1] - series.iloc[-10]) if len(series) >= 10 else 0.0

    if current > avg * 1.15 and change_10d > 0.20:
        return {
            "vote": -1,
            "reason": f"High-yield credit spreads elevated ({current:.2f} vs {lookback_days}d avg "
                      f"{avg:.2f}) and widening (+{change_10d:.2f} over 10d) — credit markets "
                      f"pricing in rising risk, often leads equity weakness",
        }
    if current < avg * 0.90 and change_10d < -0.10:
        return {
            "vote": 1,
            "reason": f"High-yield credit spreads tight ({current:.2f}) and narrowing "
                      f"({change_10d:.2f} over 10d) — credit markets calm, supportive of risk assets",
        }
    return {"vote": 0, "reason": f"Credit spreads roughly stable (current={current:.2f}, "
                                   f"avg={avg:.2f})"}


def get_fred_macro_votes() -> List[Dict[str, Any]]:
    """
    Convenience wrapper: runs both FRED-based indicators and returns their
    votes as a list, ready to extend() into market_regime_overlay.py's
    vote list alongside macro_cross_asset.py's existing 3 and
    volatility_clustering.py's GARCH vote.
    """
    return [
        yield_curve_indicator(),
        credit_spread_indicator(),
    ]
