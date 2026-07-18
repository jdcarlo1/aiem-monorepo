"""
aiem_multitimeframe.py — Multi-Timeframe Analysis for the Standalone Options Engine

Analyzes 12 timeframes from Polygon (8 weighted + 4 entry-timing-only).

Weighted timeframes (sum = 1.0):
  Monthly           → 0.25
  Weekly            → 0.25
  Daily             → 0.20
  4H / 1H           → 0.10 each
  30m / 15m         → 0.04 each
  5m                → 0.02

Entry-timing-only (not weighted in alignment score):
  4m / 3m / 2m / 1m → resolution check; READY requires ≥50% agree with dominant bias

Total timeframes: 12 (Monthly, Weekly, Daily, 4H, 1H, 30m, 15m, 5m, 4m, 3m, 2m, 1m)

Returns per ticker:
  timeframe_alignment_score   [0,1]  — weighted agreement across 8 scored TFs
  conflict_score              [0,1]  — proportion of conflicting scored TFs
  dominant_bias               BULLISH | BEARISH | NEUTRAL
  entry_timing_status         READY | WAIT | CONFLICTED | INSUFFICIENT_DATA
  entry_timing_tfs            dict with 1m/2m/3m/4m directions
  timeframes_json             full per-TF breakdown

Stores results in options_engine_mtf (UNIQUE ticker+run_date).
"""

import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, date, time, timedelta
from typing import Optional

import pytz
import psycopg2

log = logging.getLogger("aiem_multitimeframe")

_ET          = pytz.timezone("America/New_York")
_DB_URL      = os.environ.get("DATABASE_URL", "")
_POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
_BASE        = "https://api.polygon.io"

# Timeframe weights (must sum to 1.0)
_TF_WEIGHTS = {
    "monthly": 0.25,
    "weekly":  0.25,
    "daily":   0.20,
    "4h":      0.10,
    "1h":      0.10,
    "30m":     0.04,
    "15m":     0.04,
    "5m":      0.02,
    # 1m is entry-timing only, excluded from alignment score
}


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON FETCH
# ─────────────────────────────────────────────────────────────────────────────

def _polygon_get(path: str, params: dict) -> Optional[dict]:
    params["apiKey"] = _POLYGON_KEY
    url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aiem-mtf/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception as e:
        log.debug(f"[polygon_get] {path} error: {e}")
        return None


def _fetch_bars(ticker: str, multiplier: int, timespan: str,
                from_date: str, to_date: str, limit: int = 200) -> list:
    """
    Fetch aggregate bars.
    multiplier: 1,4,30,15,5,1
    timespan:   minute|hour|day|week|month
    from_date / to_date: "YYYY-MM-DD"
    """
    resp = _polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{to_date}",
        {"adjusted": "true", "sort": "asc", "limit": str(limit)},
    )
    if not resp or resp.get("resultsCount", 0) == 0:
        return []
    return resp.get("results", [])


def _fetch_bars_ms(ticker: str, multiplier: int, timespan: str,
                   from_ms: int, to_ms: int, limit: int = 120) -> list:
    """Fetch bars with millisecond timestamps (for intraday)."""
    resp = _polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_ms}/{to_ms}",
        {"adjusted": "true", "sort": "asc", "limit": str(limit)},
    )
    if not resp or resp.get("resultsCount", 0) == 0:
        return []
    return resp.get("results", [])


# ─────────────────────────────────────────────────────────────────────────────
# TREND ANALYSIS PER TIMEFRAME
# ─────────────────────────────────────────────────────────────────────────────

def _sma(closes: list, n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _analyze_bars(bars: list, tf_name: str) -> dict:
    """
    Given a list of OHLCV bars, return:
      direction   BULLISH | BEARISH | NEUTRAL
      strength    [0,1] (how strong the trend is)
      score       [0,1] (directional score for alignment calc)
      sma20_pos   ABOVE | BELOW | FLAT | N/A
      last_close  float
      bar_count   int
    """
    n = len(bars)
    if n < 3:
        return {
            "direction": "NEUTRAL", "strength": 0.0,
            "score": 0.5, "sma20_pos": "N/A",
            "last_close": None, "bar_count": n,
            "status": "INSUFFICIENT_DATA",
        }

    closes = [b["c"] for b in bars]
    last   = closes[-1]

    # SMA20 (or use all bars if <20)
    sma_n = min(20, n)
    sma20 = _sma(closes, sma_n)

    # Short-term momentum: last 3 bars
    st_momentum = (closes[-1] - closes[-3]) / closes[-3] if closes[-3] else 0.0

    # Medium-term: last close vs first close
    mt_change = (last - closes[0]) / closes[0] if closes[0] else 0.0

    # Price position relative to bar range (last bar)
    last_bar = bars[-1]
    bar_range = last_bar["h"] - last_bar["l"]
    close_pos = ((last_bar["c"] - last_bar["l"]) / bar_range
                 if bar_range > 0 else 0.5)

    # SMA position
    if sma20:
        sma_pct = (last - sma20) / sma20
        sma20_pos = "ABOVE" if sma_pct > 0.005 else ("BELOW" if sma_pct < -0.005 else "FLAT")
    else:
        sma20_pos = "N/A"
        sma_pct = 0.0

    # Composite directional signal [-1, +1]
    signal = (
        _clamp(mt_change * 10,  -1, 1) * 0.40 +   # medium-term direction
        _clamp(st_momentum * 15, -1, 1) * 0.30 +   # short-term momentum
        _clamp(sma_pct * 20,    -1, 1) * 0.20 +    # SMA position
        (close_pos * 2 - 1)            * 0.10       # last bar position
    )

    # Strength = |signal|
    strength = round(min(1.0, abs(signal)), 4)

    # Direction
    if signal > 0.15:
        direction = "BULLISH"
    elif signal < -0.15:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # Score [0,1]: BULLISH=high, BEARISH=low, NEUTRAL=0.5
    score = round(_clamp((signal + 1) / 2, 0, 1), 4)

    return {
        "direction":  direction,
        "strength":   strength,
        "score":      score,
        "sma20_pos":  sma20_pos,
        "last_close": round(last, 4),
        "bar_count":  n,
        "mt_change":  round(mt_change, 5),
        "st_momentum":round(st_momentum, 5),
        "status":     "OK",
    }


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────────────
# DAILY FROM DB (avoids extra Polygon call)
# ─────────────────────────────────────────────────────────────────────────────

def _daily_bars_from_db(ticker: str, lookback_days: int = 60) -> list:
    """Pull recent daily bars from polygon_market_daily DB table."""
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT scan_date, open_price, high_price, low_price, close_price, volume
                FROM polygon_market_daily
                WHERE ticker=%s
                ORDER BY scan_date DESC
                LIMIT %s
            """, (ticker, lookback_days))
            rows = cur.fetchall()
            # Return in ascending order
            return [
                {"t": int(r[0].strftime("%s")) * 1000,
                 "o": float(r[1]), "h": float(r[2]),
                 "l": float(r[3]), "c": float(r[4]),
                 "v": float(r[5])}
                for r in reversed(rows)
            ]
    except Exception as e:
        log.warning(f"[daily_from_db] {ticker}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# INTRADAY MS HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _intraday_from_ms(run_date: date, start_h: int = 9, start_m: int = 30) -> int:
    dt = _ET.localize(datetime.combine(run_date, time(start_h, start_m)))
    return int(dt.timestamp() * 1000)


def _intraday_to_ms(run_date: date) -> int:
    dt = _ET.localize(datetime.combine(run_date, time(16, 0)))
    return int(dt.timestamp() * 1000)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY: analyze_ticker
# ─────────────────────────────────────────────────────────────────────────────

def analyze_ticker(ticker: str, run_date: date = None, store: bool = True) -> dict:
    """
    Fetch and analyze all timeframes for ticker on run_date.

    Returns:
      timeframe_alignment_score  [0,1]
      conflict_score             [0,1]
      dominant_bias              BULLISH | BEARISH | NEUTRAL
      entry_timing_status        READY | WAIT | CONFLICTED | INSUFFICIENT_DATA
      timeframes                 dict of per-TF analysis
      weights_used               dict
    """
    run_date = run_date or date.today()
    today_str = run_date.isoformat()

    # Date ranges per timeframe
    monthly_from = (run_date - timedelta(days=730)).isoformat()   # 2 years
    weekly_from  = (run_date - timedelta(days=365)).isoformat()   # 1 year
    day_4h_from  = (run_date - timedelta(days=45)).isoformat()    # 45 days
    day_1h_from  = (run_date - timedelta(days=21)).isoformat()    # 3 weeks
    day_30m_from = (run_date - timedelta(days=10)).isoformat()    # 10 days
    day_15m_from = (run_date - timedelta(days=5)).isoformat()     # 5 days
    day_5m_from  = (run_date - timedelta(days=3)).isoformat()     # 3 days

    # Intraday ms windows
    from_ms = _intraday_from_ms(run_date)
    to_ms   = _intraday_to_ms(run_date)
    # For premarket 1m (entry timing)
    pm_from_ms = _intraday_from_ms(run_date, 4, 0)

    tf_results: dict[str, dict] = {}

    # Monthly
    monthly_bars = _fetch_bars(ticker, 1, "month", monthly_from, today_str, 24)
    tf_results["monthly"] = _analyze_bars(monthly_bars, "monthly")

    # Weekly
    weekly_bars = _fetch_bars(ticker, 1, "week", weekly_from, today_str, 52)
    tf_results["weekly"] = _analyze_bars(weekly_bars, "weekly")

    # Daily (from DB — no extra Polygon call)
    daily_bars = _daily_bars_from_db(ticker, 60)
    tf_results["daily"] = _analyze_bars(daily_bars, "daily")

    # 4H
    bars_4h = _fetch_bars(ticker, 4, "hour", day_4h_from, today_str, 120)
    tf_results["4h"] = _analyze_bars(bars_4h, "4h")

    # 1H
    bars_1h = _fetch_bars(ticker, 1, "hour", day_1h_from, today_str, 120)
    tf_results["1h"] = _analyze_bars(bars_1h, "1h")

    # 30m
    bars_30m = _fetch_bars(ticker, 30, "minute", day_30m_from, today_str, 120)
    tf_results["30m"] = _analyze_bars(bars_30m, "30m")

    # 15m
    bars_15m = _fetch_bars(ticker, 15, "minute", day_15m_from, today_str, 120)
    tf_results["15m"] = _analyze_bars(bars_15m, "15m")

    # 5m
    bars_5m = _fetch_bars(ticker, 5, "minute", day_5m_from, today_str, 120)
    tf_results["5m"] = _analyze_bars(bars_5m, "5m")

    # 1m / 2m / 3m / 4m — entry timing only (today's session or premarket)
    bars_1m = _fetch_bars_ms(ticker, 1, "minute", pm_from_ms, to_ms, 120)
    tf_results["1m"] = _analyze_bars(bars_1m, "1m")
    tf_results["1m"]["role"] = "entry_timing_only"

    bars_2m = _fetch_bars_ms(ticker, 2, "minute", pm_from_ms, to_ms, 60)
    tf_results["2m"] = _analyze_bars(bars_2m, "2m")
    tf_results["2m"]["role"] = "entry_timing_only"

    bars_3m = _fetch_bars_ms(ticker, 3, "minute", pm_from_ms, to_ms, 60)
    tf_results["3m"] = _analyze_bars(bars_3m, "3m")
    tf_results["3m"]["role"] = "entry_timing_only"

    bars_4m = _fetch_bars_ms(ticker, 4, "minute", pm_from_ms, to_ms, 60)
    tf_results["4m"] = _analyze_bars(bars_4m, "4m")
    tf_results["4m"]["role"] = "entry_timing_only"

    # ── Alignment score (weighted, excluding entry-timing TFs) ───────────────
    total_weight  = 0.0
    weighted_score = 0.0
    bullish_count  = 0
    bearish_count  = 0
    neutral_count  = 0
    insufficient   = 0

    for tf_name, weight in _TF_WEIGHTS.items():
        tf = tf_results.get(tf_name, {})
        if tf.get("status") == "INSUFFICIENT_DATA":
            insufficient += 1
            continue
        score = tf.get("score", 0.5)
        weighted_score += score * weight
        total_weight   += weight
        d = tf.get("direction", "NEUTRAL")
        if d == "BULLISH":
            bullish_count += 1
        elif d == "BEARISH":
            bearish_count += 1
        else:
            neutral_count += 1

    if total_weight > 0:
        alignment_raw = weighted_score / total_weight
    else:
        alignment_raw = 0.5

    # Alignment score: distance from 0.5, scaled to [0,1]
    # 0.5 = perfectly aligned (all agree in one direction)
    # 0.5 = no alignment (split)
    # We report "how aligned" = how far from 0.5 (both directions count as aligned)
    # Re-express: alignment_score = raw_weighted_score itself [0,1]
    alignment_score = round(alignment_raw, 4)

    # Conflict score: proportion of TFs that disagree with dominant direction
    n_scored = bullish_count + bearish_count + neutral_count
    if n_scored > 0:
        dominant_count = max(bullish_count, bearish_count, neutral_count)
        conflict_score = round(1.0 - dominant_count / n_scored, 4)
    else:
        conflict_score = 1.0

    # Dominant bias
    if bullish_count > bearish_count and alignment_raw > 0.55:
        dominant_bias = "BULLISH"
    elif bearish_count > bullish_count and alignment_raw < 0.45:
        dominant_bias = "BEARISH"
    else:
        dominant_bias = "NEUTRAL"

    # Entry timing status (based on 5m + all 4 entry-timing TFs: 1m/2m/3m/4m)
    # READY: 5m agrees with dominant bias AND ≥50% of valid entry-timing TFs agree.
    # This prevents any single entry-timing TF from triggering READY alone.
    tf_5m = tf_results.get("5m", {})
    _entry_tf_names = ("1m", "2m", "3m", "4m")
    entry_tfs_all  = [tf_results.get(t, {}) for t in _entry_tf_names]
    entry_tfs_valid = [t for t in entry_tfs_all if t.get("status") != "INSUFFICIENT_DATA"]
    if insufficient >= 3:
        entry_timing = "INSUFFICIENT_DATA"
    elif conflict_score >= 0.50:
        entry_timing = "CONFLICTED"
    elif tf_5m.get("direction") == dominant_bias and (
            not entry_tfs_valid or
            sum(1 for t in entry_tfs_valid
                if t.get("direction") in (dominant_bias, "NEUTRAL"))
            / len(entry_tfs_valid) >= 0.50):
        entry_timing = "READY"
    else:
        entry_timing = "WAIT"

    # Collect entry-timing-only directions for transparency
    entry_timing_tfs = {
        t: tf_results.get(t, {}).get("direction", "INSUFFICIENT_DATA")
        for t in _entry_tf_names
    }

    result = {
        "timeframe_alignment_score": alignment_score,
        "conflict_score":            conflict_score,
        "dominant_bias":             dominant_bias,
        "entry_timing_status":       entry_timing,
        "entry_timing_tfs":          entry_timing_tfs,
        "bullish_tf_count":          bullish_count,
        "bearish_tf_count":          bearish_count,
        "neutral_tf_count":          neutral_count,
        "insufficient_tf_count":     insufficient,
        "timeframes":                tf_results,
        "weights_used":              _TF_WEIGHTS,
    }

    if store:
        _store_mtf(ticker, run_date, result)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# DB PERSIST
# ─────────────────────────────────────────────────────────────────────────────

def _store_mtf(ticker: str, run_date: date, result: dict) -> None:
    try:
        # Strip timeframes from top-level for storage efficiency
        tf_json = json.dumps({
            k: {sk: sv for sk, sv in v.items() if sk != "weights_used"}
            for k, v in result.get("timeframes", {}).items()
        })
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO options_engine_mtf (
                    ticker, run_date, alignment_score, conflict_score,
                    dominant_bias, entry_timing_status, timeframes_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, run_date) DO UPDATE SET
                    alignment_score=EXCLUDED.alignment_score,
                    conflict_score=EXCLUDED.conflict_score,
                    dominant_bias=EXCLUDED.dominant_bias,
                    entry_timing_status=EXCLUDED.entry_timing_status,
                    timeframes_json=EXCLUDED.timeframes_json
            """, (
                ticker, run_date,
                result["timeframe_alignment_score"],
                result["conflict_score"],
                result["dominant_bias"],
                result["entry_timing_status"],
                tf_json,
            ))
            conn.commit()
    except Exception as e:
        log.warning(f"[_store_mtf] {ticker} {run_date}: {e}")
