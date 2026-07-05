"""
alpha_feature_engineering.py

Enhanced feature set for AIEM's alpha-prediction model.
Target: does this stock generate excess return vs SPY over the holding period?

New features vs the base feature_engineering.py:
  - sector_relative_strength_10d  (stock vs sector ETF, 10-day window)
  - earnings_revision_score       (+1 more upgrades, -1 more downgrades, 0 neutral)
  - price_vs_52w_high             (proximity to 52-week high, 0-1)
  - momentum_5d                   (5-day return, from polygon_market_daily)
  - momentum_20d                  (20-day return)
  - rvol                          (relative volume)
  - gap_pct                       (gap from prior close)
  - float_short_pct               (short interest as % of float, if available)
  - conviction_score              (encoded conviction level)
  - vol_oi                        (volume/OI ratio for options picks)

All features return NaN gracefully if data is unavailable.
"""

import numpy as np
import os
import psycopg2
from datetime import date, timedelta

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

_DB_URL = os.environ.get("DATABASE_URL", "")

ALPHA_FEATURE_COLUMNS = [
    "rvol",
    "gap_pct",
    "momentum_5d",
    "momentum_20d",
    "price_vs_52w_high",
    "sector_relative_strength_10d",
    "earnings_revision_score",
    "conviction_score",
    "vol_oi",
    "float_short_pct",
    "day_of_week",
    "total_pts",
]


def _encode_conviction(conviction_str: str) -> float:
    mapping = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}
    if not conviction_str:
        return np.nan
    return mapping.get(str(conviction_str).upper(), np.nan)


def get_momentum(ticker: str, signal_date: date, window: int) -> float:
    """
    N-day price momentum from polygon_market_daily.
    Returns % change over `window` trading days ending on signal_date.
    """
    if not _DB_URL:
        return np.nan
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT close_price FROM polygon_market_daily
                WHERE ticker = %s AND scan_date <= %s
                ORDER BY scan_date DESC
                LIMIT %s
            """, (ticker.upper(), signal_date, window + 1))
            rows = cur.fetchall()
        if len(rows) < 2:
            return np.nan
        latest = float(rows[0][0])
        oldest = float(rows[-1][0])
        if oldest == 0:
            return np.nan
        return round((latest / oldest - 1) * 100, 4)
    except Exception:
        return np.nan


def get_price_vs_52w_high(ticker: str, signal_date: date) -> float:
    """
    Current price as a fraction of the 52-week high (0-1).
    1.0 = at the 52-week high, 0.5 = 50% below it.
    """
    if not _DB_URL:
        return np.nan
    try:
        start = signal_date - timedelta(days=365)
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(close_price), close_price
                FROM polygon_market_daily
                WHERE ticker = %s AND scan_date BETWEEN %s AND %s
                GROUP BY close_price
                ORDER BY scan_date DESC
                LIMIT 1
            """, (ticker.upper(), start, signal_date))
            row = cur.fetchone()
        if not row or not row[0]:
            return np.nan
        high52 = float(row[0])
        cur_price = float(row[1])
        return round(cur_price / high52, 4) if high52 > 0 else np.nan
    except Exception:
        return np.nan


def get_price_vs_52w_high_v2(ticker: str, signal_date: date) -> float:
    """Fixed version — separate queries for max high and current price."""
    if not _DB_URL:
        return np.nan
    try:
        start = signal_date - timedelta(days=365)
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(close_price) FROM polygon_market_daily
                WHERE ticker = %s AND scan_date BETWEEN %s AND %s
            """, (ticker.upper(), start, signal_date))
            high_row = cur.fetchone()

            cur.execute("""
                SELECT close_price FROM polygon_market_daily
                WHERE ticker = %s AND scan_date <= %s
                ORDER BY scan_date DESC LIMIT 1
            """, (ticker.upper(), signal_date))
            cur_row = cur.fetchone()

        if not high_row or not cur_row or not high_row[0]:
            return np.nan
        high52 = float(high_row[0])
        cur_price = float(cur_row[0])
        return round(cur_price / high52, 4) if high52 > 0 else np.nan
    except Exception:
        return np.nan


def get_earnings_revision_score(ticker: str) -> float:
    """
    Analyst upgrade/downgrade direction over the last 90 days.
    Returns:
      +1.0  more upgrades than downgrades
       0.0  roughly balanced or no data
      -1.0  more downgrades than upgrades
    Uses yfinance upgrades_downgrades (current-only, not historical).
    """
    if not HAS_YF:
        return np.nan
    try:
        t = yf.Ticker(ticker)
        hist = t.upgrades_downgrades
        if hist is None or len(hist) == 0:
            return 0.0

        cutoff = (date.today() - timedelta(days=90)).isoformat()
        if hasattr(hist.index, "tz_localize"):
            recent = hist[hist.index >= cutoff]
        else:
            recent = hist.tail(20)

        if len(recent) == 0:
            return 0.0

        actions = recent["Action"].str.lower() if "Action" in recent.columns else recent.iloc[:, 0].str.lower()
        upgrades   = actions.str.contains("up|buy|outperform|overweight|strong buy", na=False).sum()
        downgrades = actions.str.contains("down|sell|underperform|underweight", na=False).sum()

        if upgrades > downgrades:
            return 1.0
        elif downgrades > upgrades:
            return -1.0
        else:
            return 0.0
    except Exception:
        return np.nan


def get_float_short_pct(ticker: str) -> float:
    """
    Short interest as % of float from yfinance fast_info.
    Returns NaN if unavailable.
    """
    if not HAS_YF:
        return np.nan
    try:
        t = yf.Ticker(ticker)
        info = t.info
        short_pct = info.get("shortPercentOfFloat")
        if short_pct is not None:
            return round(float(short_pct) * 100, 2)
        return np.nan
    except Exception:
        return np.nan


def build_alpha_feature_row(pick: dict) -> dict:
    """
    Build one feature row for the alpha model.

    pick dict expected keys (all optional — NaN returned if missing):
      ticker, trade_date, signal_source, conviction, total_pts,
      rvol, gap_pct, vol_oi, signal_detail
    """
    ticker      = pick.get("ticker", "")
    trade_date  = pick.get("trade_date") or date.today()
    if isinstance(trade_date, str):
        import datetime
        trade_date = datetime.date.fromisoformat(trade_date)

    from sector_etf_data import get_sector_relative_strength

    row = {
        "rvol":                       float(pick.get("rvol") or np.nan),
        "gap_pct":                    float(pick.get("gap_pct") or np.nan),
        "momentum_5d":                get_momentum(ticker, trade_date, 5),
        "momentum_20d":               get_momentum(ticker, trade_date, 20),
        "price_vs_52w_high":          get_price_vs_52w_high_v2(ticker, trade_date),
        "sector_relative_strength_10d": get_sector_relative_strength(ticker, trade_date, 10) or np.nan,
        "earnings_revision_score":    np.nan,
        "conviction_score":           _encode_conviction(pick.get("conviction")),
        "vol_oi":                     float(pick.get("vol_oi") or np.nan),
        "float_short_pct":            np.nan,
        "day_of_week":                float(trade_date.weekday()),
        "total_pts":                  float(pick.get("total_pts") or np.nan),
    }

    return row


def build_alpha_feature_row_with_live(pick: dict) -> dict:
    """
    Full feature row including live yfinance calls (earnings revision, float/short).
    Use at pick time — NOT during bulk training (too slow).
    """
    row = build_alpha_feature_row(pick)
    ticker = pick.get("ticker", "")
    if ticker:
        row["earnings_revision_score"] = get_earnings_revision_score(ticker)
        row["float_short_pct"]         = get_float_short_pct(ticker)
    return row
