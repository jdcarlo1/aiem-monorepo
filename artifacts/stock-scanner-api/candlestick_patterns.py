"""
candlestick_patterns.py
====================================================================
Detects common candlestick patterns from OHLC data: doji, hammer,
shooting star, bullish/bearish engulfing. Pure price-action logic,
no external dependencies beyond what's already in the codebase.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, List, Optional

import psycopg2
import psycopg2.extras


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return h - l if h > l else 0.0001  # avoid div by zero


def is_doji(o: float, h: float, l: float, c: float, threshold: float = 0.1) -> bool:
    """Body is a small fraction of the total range — indecision candle."""
    rng = _range(h, l)
    return _body(o, c) / rng <= threshold


def is_hammer(o: float, h: float, l: float, c: float) -> bool:
    """Small body near the top, long lower wick — potential bullish reversal."""
    rng = _range(h, l)
    body = _body(o, c)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return body / rng < 0.3 and lower_wick > 2 * body and upper_wick < body


def is_shooting_star(o: float, h: float, l: float, c: float) -> bool:
    """Small body near the bottom, long upper wick — potential bearish reversal."""
    rng = _range(h, l)
    body = _body(o, c)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return body / rng < 0.3 and upper_wick > 2 * body and lower_wick < body


def is_bullish_engulfing(prev: Dict[str, float], curr: Dict[str, float]) -> bool:
    """Previous candle bearish, current candle bullish and fully engulfs it."""
    prev_bearish = prev["close"] < prev["open"]
    curr_bullish = curr["close"] > curr["open"]
    engulfs = curr["open"] <= prev["close"] and curr["close"] >= prev["open"]
    return prev_bearish and curr_bullish and engulfs


def is_bearish_engulfing(prev: Dict[str, float], curr: Dict[str, float]) -> bool:
    prev_bullish = prev["close"] > prev["open"]
    curr_bearish = curr["close"] < curr["open"]
    engulfs = curr["open"] >= prev["close"] and curr["close"] <= prev["open"]
    return prev_bullish and curr_bearish and engulfs


def detect_patterns(ohlc_bars: List[Dict[str, float]]) -> Dict[str, Any]:
    """
    ohlc_bars: list of dicts with keys open/high/low/close, oldest first.
    Returns patterns detected on the MOST RECENT bar (and prior bar for
    two-candle patterns like engulfing).
    """
    if len(ohlc_bars) < 2:
        return {"patterns": [], "error": "need at least 2 bars"}

    curr = ohlc_bars[-1]
    prev = ohlc_bars[-2]
    patterns = []

    if is_doji(curr["open"], curr["high"], curr["low"], curr["close"]):
        patterns.append("doji")
    if is_hammer(curr["open"], curr["high"], curr["low"], curr["close"]):
        patterns.append("hammer")
    if is_shooting_star(curr["open"], curr["high"], curr["low"], curr["close"]):
        patterns.append("shooting_star")
    if is_bullish_engulfing(prev, curr):
        patterns.append("bullish_engulfing")
    if is_bearish_engulfing(prev, curr):
        patterns.append("bearish_engulfing")

    return {
        "patterns": patterns,
        "checked_at": dt.datetime.utcnow().isoformat(),
        "bar_close": curr["close"],
    }


def get_patterns_for_ticker(db_url: str, ticker: str, lookback: int = 5) -> Dict[str, Any]:
    """
    Pulls recent OHLC bars for a ticker from your price history table
    and runs pattern detection. ADJUST table/column names to match your
    actual schema.
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT open_price AS open, high_price AS high,
                       low_price AS low, close_price AS close
                FROM price_history
                WHERE ticker = %s
                ORDER BY trade_date ASC
                LIMIT %s
            """, (ticker, lookback))
            bars = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not bars:
        return {"patterns": [], "error": f"no price data for {ticker}"}

    result = detect_patterns(bars)
    result["ticker"] = ticker
    return result


if __name__ == "__main__":
    sample_bars = [
        {"open": 100, "high": 102, "low": 98, "close": 99},
        {"open": 99, "high": 105, "low": 97, "close": 104},
    ]
    print(detect_patterns(sample_bars))
