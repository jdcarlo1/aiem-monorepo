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


def _preceding_trend(bars: List[Dict[str, float]], lookback: int = 5) -> str:
    """Cheap trend read on the bars BEFORE the candle being classified —
    needed to tell a hammer (downtrend) from a hanging man (uptrend), since
    both share identical single-candle geometry."""
    ctx = bars[-(lookback + 1):-1]
    if len(ctx) < 2:
        return "flat"
    net = ctx[-1]["close"] - ctx[0]["close"]
    if net > 0:
        return "uptrend"
    if net < 0:
        return "downtrend"
    return "flat"


def is_hanging_man(o: float, h: float, l: float, c: float, preceding_trend: str) -> bool:
    """Same geometry as a hammer, but bearish-reversal significance because
    it appears after an uptrend rather than a downtrend."""
    return is_hammer(o, h, l, c) and preceding_trend == "uptrend"


def is_morning_star(bars: List[Dict[str, float]]) -> bool:
    """3-candle bullish reversal: long bearish, small-bodied star, long
    bullish closing back into the first candle's body."""
    if len(bars) < 3:
        return False
    c1, c2, c3 = bars[-3], bars[-2], bars[-1]
    c1_bearish = c1["close"] < c1["open"]
    c1_body = _body(c1["open"], c1["close"])
    c2_body = _body(c2["open"], c2["close"])
    c3_bullish = c3["close"] > c3["open"]
    small_star = c1_body > 0 and c2_body < c1_body * 0.5
    deep_close = c3["close"] > (c1["open"] + c1["close"]) / 2
    return bool(c1_bearish and small_star and c3_bullish and deep_close)


def is_evening_star(bars: List[Dict[str, float]]) -> bool:
    """3-candle bearish reversal: mirror of morning star."""
    if len(bars) < 3:
        return False
    c1, c2, c3 = bars[-3], bars[-2], bars[-1]
    c1_bullish = c1["close"] > c1["open"]
    c1_body = _body(c1["open"], c1["close"])
    c2_body = _body(c2["open"], c2["close"])
    c3_bearish = c3["close"] < c3["open"]
    small_star = c1_body > 0 and c2_body < c1_body * 0.5
    deep_close = c3["close"] < (c1["open"] + c1["close"]) / 2
    return bool(c1_bullish and small_star and c3_bearish and deep_close)


def detect_patterns(ohlc_bars: List[Dict[str, float]]) -> Dict[str, Any]:
    """
    ohlc_bars: list of dicts with keys open/high/low/close, oldest first.
    Returns patterns detected on the MOST RECENT bar (single-candle),
    the prior bar for two-candle patterns (engulfing, hanging man needs
    trend context), and the prior two bars for three-candle patterns
    (morning/evening star).
    """
    if len(ohlc_bars) < 2:
        return {"patterns": [], "error": "need at least 2 bars"}

    curr = ohlc_bars[-1]
    prev = ohlc_bars[-2]
    patterns = []

    trend = _preceding_trend(ohlc_bars)
    if is_doji(curr["open"], curr["high"], curr["low"], curr["close"]):
        patterns.append("doji")
    if is_hammer(curr["open"], curr["high"], curr["low"], curr["close"]) and trend != "uptrend":
        patterns.append("hammer")
    if is_hanging_man(curr["open"], curr["high"], curr["low"], curr["close"], trend):
        patterns.append("hanging_man")
    if is_shooting_star(curr["open"], curr["high"], curr["low"], curr["close"]):
        patterns.append("shooting_star")
    if is_bullish_engulfing(prev, curr):
        patterns.append("bullish_engulfing")
    if is_bearish_engulfing(prev, curr):
        patterns.append("bearish_engulfing")
    if is_morning_star(ohlc_bars):
        patterns.append("morning_star")
    if is_evening_star(ohlc_bars):
        patterns.append("evening_star")

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
