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

POINT-IN-TIME / VINTAGE INTEGRITY (honest, current limitation — Diagram 2
C7 remediation, 2026-07-10)
--------------------------------------------------------------------------
This module deliberately does NOT implement true point-in-time vintage
handling. The CSV endpoint above always returns the LATEST-REVISED value
for every historical date, not the value that was actually known/released
on that date. FRED does publish a vintage-aware API (ALFRED, via
`realtime_start`/`realtime_end` params on
https://api.stlouisfed.org/fred/series/observations), but that endpoint
requires a free FRED_API_KEY that is not currently provisioned in this
project's secrets, and building a full historical vintage backfill is a
separate, larger data-acquisition project, not a one-line fix.

What IS implemented instead, so a genuine limitation is never silently
mislabeled as full point-in-time integrity: every value persisted to
`regime_history` is stamped with `recorded_at` (the real wall-clock time
this process fetched it) and an explicit `vintage='latest_revised'` tag.
This makes every row honestly self-describing — a reader of the table can
tell, without guessing, that historical FRED values here are NOT
as-of-original-release and should not be used as ground truth in a
backtest that requires point-in-time integrity. A future ALFRED
integration (once FRED_API_KEY is provisioned) would populate rows with
`vintage='alfred_realtime'` instead, and both vintages could coexist in
the same table.

INTEGRATION
-----------
Same vote contract as macro_cross_asset.py and market_regime_overlay.py's
other indicators: {"vote": -1/0/1, "reason": str}.
from fred_macro import get_fred_macro_votes
votes.extend(get_fred_macro_votes())
"""

import io
import os
import datetime as dt
from typing import Dict, Any, List, Optional

import pandas as pd
import requests

try:
    import psycopg2
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

_REGIME_TABLE_READY = False


def _init_regime_history() -> None:
    """Create regime_history table if it does not exist."""
    global _REGIME_TABLE_READY
    if _REGIME_TABLE_READY or not _DB_AVAILABLE:
        return
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return
    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS regime_history (
                        id          SERIAL PRIMARY KEY,
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        series_id   TEXT        NOT NULL,
                        raw_value   FLOAT,
                        vote        INTEGER,
                        regime_label TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_regime_history_recorded
                        ON regime_history (recorded_at DESC);
                    ALTER TABLE regime_history
                        ADD COLUMN IF NOT EXISTS vintage TEXT NOT NULL DEFAULT 'latest_revised';
                """)
            conn.commit()
        _REGIME_TABLE_READY = True
    except Exception as _e:
        pass


def _persist_regime_vote(series_id: str, raw_value: float,
                          vote: int, regime_label: str,
                          vintage: str = "latest_revised") -> None:
    """
    Insert a single regime vote row into regime_history.

    `vintage` is an honest, self-describing tag for the C7 point-in-time
    limitation documented in this module's header: 'latest_revised' means
    this value is FRED's current-revision figure fetched at `recorded_at`
    wall-clock time, NOT the value as-originally-released on the series'
    reference date. A future ALFRED-backed fetch path would pass
    vintage='alfred_realtime' here instead.
    """
    if not _DB_AVAILABLE:
        return
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return
    try:
        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO regime_history (series_id, raw_value, vote, regime_label, vintage) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (series_id, raw_value, vote, regime_label, vintage),
                )
            conn.commit()
    except Exception:
        pass


_init_regime_history()

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
        result = {
            "vote": -1,
            "reason": f"Yield curve inverted ({current:.2f}, {pct_days_inverted:.0%} of last "
                      f"{lookback_days}d) — historically the single most reliable recession-risk "
                      f"indicator; this is a standing macro caution, not a day-trade signal",
        }
    elif current > 0 and trend_20d > 0.10:
        result = {
            "vote": 1,
            "reason": f"Yield curve positively sloped ({current:.2f}) and steepening "
                      f"(+{trend_20d:.2f} over 20d) — consistent with healthy expansion",
        }
    else:
        result = {"vote": 0, "reason": f"Yield curve neutral/mixed (current={current:.2f}, "
                                        f"{pct_days_inverted:.0%} inverted over {lookback_days}d)"}
    label = "inverted" if current < 0 else ("steepening" if trend_20d > 0.10 else "neutral")
    _persist_regime_vote("T10Y2Y", current, result["vote"], label)
    return result


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
        result = {
            "vote": -1,
            "reason": f"High-yield credit spreads elevated ({current:.2f} vs {lookback_days}d avg "
                      f"{avg:.2f}) and widening (+{change_10d:.2f} over 10d) — credit markets "
                      f"pricing in rising risk, often leads equity weakness",
        }
    elif current < avg * 0.90 and change_10d < -0.10:
        result = {
            "vote": 1,
            "reason": f"High-yield credit spreads tight ({current:.2f}) and narrowing "
                      f"({change_10d:.2f} over 10d) — credit markets calm, supportive of risk assets",
        }
    else:
        result = {"vote": 0, "reason": f"Credit spreads roughly stable (current={current:.2f}, "
                                        f"avg={avg:.2f})"}
    label = "elevated_widening" if result["vote"] == -1 else ("tight_narrowing" if result["vote"] == 1 else "stable")
    _persist_regime_vote("BAMLH0A0HYM2", current, result["vote"], label)
    return result


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
