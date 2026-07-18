"""
aiem_premarket_intel.py — Premarket Intelligence for the Standalone Options Engine

Computes per candidate (4:00–9:30 AM ET bars from Polygon):
  premarket_gap          vs prev close
  premarket_high / low   range extremes
  premarket_volume       total shares traded
  pm_rvol                relative to 10-day avg premarket volume (from DB)
  pm_trend_quality       linear-regression slope r² over PM bars
  pm_support / resistance key intra-PM levels
  sector_bias            SPY/QQQ premarket direction
  premarket_score        [0,1] weighted composite
  premarket_direction    BULLISH | BEARISH | NEUTRAL
  premarket_confidence   [0,1]
  premarket_risk_flags   list[str]

Post-9:30 update (call update_intraday):
  pm_high_broken         bool
  pm_low_held            bool
  opening_volume_ok      bool
  sector_confirmed       bool
  continuation_or_rev    CONTINUATION | REVERSAL | UNCLEAR

Stores results in options_engine_premarket (UNIQUE ticker+run_date).
All Polygon calls via urllib.request (no requests package dependency).
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
import psycopg2.extras

log = logging.getLogger("aiem_premarket_intel")

_ET          = pytz.timezone("America/New_York")
_DB_URL      = os.environ.get("DATABASE_URL", "")
_POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
_BASE        = "https://api.polygon.io"
_PM_START_H  = 4     # 4:00 AM ET
_PM_END_H    = 9
_PM_END_M    = 30


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _polygon_get(path: str, params: dict) -> Optional[dict]:
    """GET a Polygon endpoint; return parsed JSON or None on error."""
    params["apiKey"] = _POLYGON_KEY
    url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aiem-options-engine/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning(f"[polygon_get] {path} error: {e}")
        return None


def _fetch_minute_bars(ticker: str, from_ms: int, to_ms: int,
                       multiplier: int = 1, limit: int = 300) -> list:
    """
    Fetch 1-minute (or N-minute) aggregate bars from Polygon.
    from_ms / to_ms are Unix milliseconds.
    Returns list of bar dicts with keys: t, o, h, l, c, v, vw.
    """
    resp = _polygon_get(
        f"/v2/aggs/ticker/{ticker}/range/{multiplier}/minute/{from_ms}/{to_ms}",
        {"adjusted": "true", "sort": "asc", "limit": str(limit)},
    )
    if not resp or resp.get("resultsCount", 0) == 0:
        return []
    return resp.get("results", [])


def _dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _pm_window(run_date: date) -> tuple[int, int]:
    """Return (from_ms, to_ms) for premarket window 4:00–9:30 AM ET."""
    pm_start = _ET.localize(datetime.combine(run_date, time(_PM_START_H, 0, 0)))
    pm_end   = _ET.localize(datetime.combine(run_date, time(_PM_END_H, _PM_END_M, 0)))
    return _dt_to_ms(pm_start), _dt_to_ms(pm_end)


def _intraday_window(run_date: date) -> tuple[int, int]:
    """Return (from_ms, to_ms) for 9:30–16:00 ET intraday window."""
    open_  = _ET.localize(datetime.combine(run_date, time(9, 30, 0)))
    close_ = _ET.localize(datetime.combine(run_date, time(16, 0, 0)))
    return _dt_to_ms(open_), _dt_to_ms(close_)


# ─────────────────────────────────────────────────────────────────────────────
# TREND / LEVEL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _linear_slope_r2(ys: list) -> tuple[float, float]:
    """Return (slope_pct_per_bar, r²) for a sequence of prices."""
    n = len(ys)
    if n < 4:
        return 0.0, 0.0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    ss_xy = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    ss_xx = sum((x - x_mean) ** 2 for x in xs)
    ss_yy = sum((y - y_mean) ** 2 for y in ys)
    if ss_xx == 0 or ss_yy == 0:
        return 0.0, 0.0
    slope = ss_xy / ss_xx
    r2    = (ss_xy ** 2) / (ss_xx * ss_yy)
    slope_pct = slope / y_mean if y_mean else 0.0
    return round(slope_pct, 6), round(r2, 4)


def _support_resistance(bars: list) -> tuple[float, float]:
    """
    Identify intra-PM support and resistance as:
      support    = lowest cluster low (20th percentile of bar lows)
      resistance = highest cluster high (80th percentile of bar highs)
    """
    if not bars:
        return 0.0, 0.0
    lows  = sorted(b["l"] for b in bars)
    highs = sorted(b["h"] for b in bars)
    n     = len(lows)
    support    = lows[max(0, int(n * 0.20))]
    resistance = highs[min(n - 1, int(n * 0.80))]
    return round(support, 2), round(resistance, 2)


# ─────────────────────────────────────────────────────────────────────────────
# SECTOR PROXY
# ─────────────────────────────────────────────────────────────────────────────

def _get_sector_proxy(run_date: date) -> dict:
    """
    Fetch SPY and QQQ premarket bars as market/sector proxy.
    Returns direction = BULLISH | BEARISH | NEUTRAL and bias_score [-1,1].
    """
    fm, tm = _pm_window(run_date)
    spy_bars = _fetch_minute_bars("SPY", fm, tm)
    qqq_bars = _fetch_minute_bars("QQQ", fm, tm)

    def _proxy_dir(bars):
        if len(bars) < 4:
            return 0.0
        open_ = bars[0]["o"]
        last  = bars[-1]["c"]
        return (last - open_) / open_ if open_ else 0.0

    spy_chg = _proxy_dir(spy_bars)
    qqq_chg = _proxy_dir(qqq_bars)
    avg_chg = (spy_chg + qqq_chg) / 2.0

    if avg_chg > 0.003:
        direction, bias = "BULLISH",  min(1.0, avg_chg * 100)
    elif avg_chg < -0.003:
        direction, bias = "BEARISH", max(-1.0, avg_chg * 100)
    else:
        direction, bias = "NEUTRAL", avg_chg * 100

    return {"direction": direction, "bias_score": round(bias, 4),
            "spy_pct": round(spy_chg * 100, 3), "qqq_pct": round(qqq_chg * 100, 3)}


# ─────────────────────────────────────────────────────────────────────────────
# AVG PREMARKET VOLUME (10-day DB lookback)
# ─────────────────────────────────────────────────────────────────────────────

def _avg_pm_volume_from_db(ticker: str, run_date: date) -> float:
    """
    Pull avg premarket_volume from options_engine_premarket for the last 10 days.
    Returns None if fewer than 3 data points.
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT AVG(premarket_volume)
                FROM options_engine_premarket
                WHERE ticker=%s
                  AND run_date BETWEEN %s AND %s
                  AND premarket_volume > 0
            """, (ticker, run_date - timedelta(days=14), run_date - timedelta(days=1)))
            row = cur.fetchone()
            return float(row[0]) if row and row[0] else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY: get_premarket_intel
# ─────────────────────────────────────────────────────────────────────────────

def get_premarket_intel(ticker: str, run_date: date = None,
                        prev_close: float = None,
                        store: bool = True) -> dict:
    """
    Compute premarket intelligence for ticker on run_date.

    Returns dict with:
      premarket_score      [0,1]
      premarket_direction  BULLISH|BEARISH|NEUTRAL
      premarket_confidence [0,1]
      premarket_risk_flags list[str]
      premarket_gap        float (fractional, e.g. 0.023 = +2.3%)
      premarket_high       float
      premarket_low        float
      premarket_volume     int
      pm_rvol              float (relative vol vs 10-day avg)
      pm_trend_quality     float (r² of price trend)
      pm_support           float
      pm_resistance        float
      sector               dict
      bars_count           int
    """
    run_date = run_date or date.today()
    risk_flags: list[str] = []

    # ── Fetch premarket bars ────────────────────────────────────────────────
    fm, tm = _pm_window(run_date)
    bars = _fetch_minute_bars(ticker, fm, tm)

    if not bars:
        return {
            "premarket_score": 0.5,
            "premarket_direction": "NEUTRAL",
            "premarket_confidence": 0.0,
            "premarket_risk_flags": ["NO_PREMARKET_BARS"],
            "bars_count": 0,
            "error": "no_premarket_bars",
        }

    pm_high   = max(b["h"] for b in bars)
    pm_low    = min(b["l"] for b in bars)
    pm_volume = int(sum(b["v"] for b in bars))
    pm_open   = bars[0]["o"]
    pm_close  = bars[-1]["c"]  # last bar close = pre-market close price

    # ── Premarket gap (vs prev close) ───────────────────────────────────────
    # If prev_close not supplied, fetch from polygon_market_daily
    if not prev_close:
        try:
            with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT close_price FROM polygon_market_daily
                    WHERE ticker=%s ORDER BY scan_date DESC LIMIT 1
                """, (ticker,))
                row = cur.fetchone()
                prev_close = float(row[0]) if row else pm_open
        except Exception:
            prev_close = pm_open

    gap = (pm_open - prev_close) / prev_close if prev_close else 0.0

    # ── RVOL ────────────────────────────────────────────────────────────────
    avg_vol = _avg_pm_volume_from_db(ticker, run_date)
    if avg_vol and avg_vol > 0:
        pm_rvol = pm_volume / avg_vol
    else:
        pm_rvol = 1.0   # no history → neutral
        risk_flags.append("PM_RVOL_NO_HISTORY")

    # ── Trend quality ───────────────────────────────────────────────────────
    closes = [b["c"] for b in bars]
    slope_pct, r2 = _linear_slope_r2(closes)

    # ── Support / Resistance ────────────────────────────────────────────────
    pm_support, pm_resistance = _support_resistance(bars)

    # ── Sector proxy ────────────────────────────────────────────────────────
    sector = _get_sector_proxy(run_date)

    # ── Risk flags ──────────────────────────────────────────────────────────
    if pm_rvol < 0.5:
        risk_flags.append("PM_LOW_VOLUME")
    if abs(gap) > 0.10:
        risk_flags.append("PM_EXTREME_GAP_GT10PCT")
    if len(bars) < 10:
        risk_flags.append("PM_SPARSE_BARS")
    if r2 < 0.20:
        risk_flags.append("PM_CHOPPY_TREND")
    if sector["direction"] != ("BULLISH" if gap > 0 else "BEARISH"):
        risk_flags.append("SECTOR_CONFLICT")

    # ── Weighted premarket score ─────────────────────────────────────────────
    # Components and weights (sum = 1.0):
    #   gap_strength      0.25
    #   volume_quality    0.20
    #   trend_quality     0.20
    #   sector_alignment  0.20
    #   price_position    0.15

    # gap_strength: normalize ±5% → [0,1]
    gap_capped = max(-0.05, min(0.05, gap))
    gap_comp   = (gap_capped + 0.05) / 0.10   # maps [-5%,+5%] → [0,1]

    # volume_quality: rvol capped at 3x → [0,1]
    vol_comp = min(1.0, pm_rvol / 3.0)

    # trend_quality: r² × direction match
    trend_dir  = 1.0 if slope_pct > 0 else -1.0
    gap_dir    = 1.0 if gap > 0 else (-1.0 if gap < 0 else 0.0)
    trend_comp = r2 * (0.5 + 0.5 * (1 if trend_dir == gap_dir else -1))

    # sector_alignment: [0,1] based on bias
    sector_aligned = (
        (sector["bias_score"] + 1) / 2   # maps [-1,1] → [0,1]
        if gap_dir >= 0 else
        (1 - (sector["bias_score"] + 1) / 2)
    )

    # price_position: PM close relative to PM range
    pm_range = pm_high - pm_low
    if pm_range > 0:
        price_pos = (pm_close - pm_low) / pm_range
    else:
        price_pos = 0.5

    raw_score = (
        gap_comp       * 0.25 +
        vol_comp       * 0.20 +
        trend_comp     * 0.20 +
        sector_aligned * 0.20 +
        price_pos      * 0.15
    )
    premarket_score = round(max(0.0, min(1.0, raw_score)), 4)

    # ── Direction & Confidence ──────────────────────────────────────────────
    if gap > 0.005 and premarket_score >= 0.55:
        pm_direction = "BULLISH"
    elif gap < -0.005 and premarket_score <= 0.45:
        pm_direction = "BEARISH"
    else:
        pm_direction = "NEUTRAL"

    # Confidence = how far from 0.5 the score is, penalized by risk flags
    raw_conf = abs(premarket_score - 0.5) * 2.0
    flag_penalty = min(0.4, len(risk_flags) * 0.10)
    pm_confidence = round(max(0.0, raw_conf - flag_penalty), 4)

    result = {
        "premarket_score":      premarket_score,
        "premarket_direction":  pm_direction,
        "premarket_confidence": pm_confidence,
        "premarket_risk_flags": risk_flags,
        "premarket_gap":        round(gap, 5),
        "premarket_high":       round(pm_high, 4),
        "premarket_low":        round(pm_low, 4),
        "premarket_volume":     pm_volume,
        "pm_rvol":              round(pm_rvol, 4),
        "pm_trend_quality":     round(r2, 4),
        "pm_slope_pct_per_bar": round(slope_pct, 6),
        "pm_support":           pm_support,
        "pm_resistance":        pm_resistance,
        "sector":               sector,
        "bars_count":           len(bars),
        "prev_close":           round(prev_close, 4),
    }

    if store:
        _store_premarket(ticker, run_date, result)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST-9:30 INTRADAY UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def update_intraday(ticker: str, run_date: date = None) -> dict:
    """
    Post-9:30 update: check break/fail of PM high/low, opening volume,
    sector confirmation, continuation vs reversal.
    Updates the options_engine_premarket row and returns the result.
    """
    run_date = run_date or date.today()

    # Fetch stored PM levels from DB
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT premarket_high, premarket_low, premarket_volume, pm_rvol,
                       premarket_direction, prev_close
                FROM options_engine_premarket
                WHERE ticker=%s AND run_date=%s
            """, (ticker, run_date))
            row = cur.fetchone()
    except Exception as e:
        return {"error": f"db_read_failed: {e}"}

    if not row:
        return {"error": "no_premarket_record"}

    pm_high, pm_low, pm_vol, pm_rvol_stored, pm_dir, prev_close = row
    pm_high = float(pm_high or 0)
    pm_low  = float(pm_low  or 0)

    # Fetch first 30 min intraday bars (9:30–10:00)
    fm, tm = _intraday_window(run_date)
    open_end = _dt_to_ms(
        _ET.localize(datetime.combine(run_date, time(10, 0, 0)))
    )
    intra_bars = _fetch_minute_bars(ticker, fm, open_end)
    if not intra_bars:
        return {"error": "no_intraday_bars"}

    intra_high  = max(b["h"] for b in intra_bars)
    intra_low   = min(b["l"] for b in intra_bars)
    intra_vol   = int(sum(b["v"] for b in intra_bars))
    intra_close = intra_bars[-1]["c"]

    pm_high_broken = intra_high > pm_high
    pm_low_held    = intra_low >= pm_low * 0.995   # 0.5% tolerance

    # Volume confirmation: opening 30min should be ≥ 15% of typical PM volume
    expected_open_vol = float(pm_vol or 0) * 0.15 if pm_vol else 0
    opening_volume_ok = intra_vol >= expected_open_vol if expected_open_vol > 0 else True

    # Sector confirmation
    sector = _get_sector_proxy(run_date)
    sector_confirmed = sector["direction"] == pm_dir

    # Continuation vs Reversal
    if pm_dir == "BULLISH":
        continuation = pm_high_broken and pm_low_held
    elif pm_dir == "BEARISH":
        continuation = not pm_high_broken and not pm_low_held
    else:
        continuation = False
    reversal = (pm_dir == "BULLISH" and not pm_high_broken and not pm_low_held)

    cont_rev = (
        "CONTINUATION" if continuation else
        "REVERSAL"     if reversal     else
        "UNCLEAR"
    )

    update_data = {
        "pm_high_broken":     pm_high_broken,
        "pm_low_held":        pm_low_held,
        "opening_volume_ok":  opening_volume_ok,
        "sector_confirmed":   sector_confirmed,
        "continuation_or_rev": cont_rev,
        "intra_high":         round(intra_high, 4),
        "intra_low":          round(intra_low,  4),
        "intra_close":        round(intra_close, 4),
        "intra_vol_30m":      intra_vol,
    }

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE options_engine_premarket
                SET pm_high_broken=%s, pm_low_held=%s,
                    intraday_updated_at=NOW(),
                    risk_flags_json = risk_flags_json || %s::jsonb
                WHERE ticker=%s AND run_date=%s
            """, (pm_high_broken, pm_low_held,
                  json.dumps({"intraday": update_data}),
                  ticker, run_date))
            conn.commit()
    except Exception as e:
        log.warning(f"[update_intraday] db update failed: {e}")

    return update_data


# ─────────────────────────────────────────────────────────────────────────────
# DB PERSIST
# ─────────────────────────────────────────────────────────────────────────────

def _store_premarket(ticker: str, run_date: date, result: dict) -> None:
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO options_engine_premarket (
                    ticker, run_date, premarket_gap, premarket_high, premarket_low,
                    premarket_volume, pm_rvol, pm_trend_quality, premarket_score,
                    premarket_direction, premarket_confidence,
                    risk_flags_json, raw_data_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, run_date) DO UPDATE SET
                    premarket_gap=EXCLUDED.premarket_gap,
                    premarket_high=EXCLUDED.premarket_high,
                    premarket_low=EXCLUDED.premarket_low,
                    premarket_volume=EXCLUDED.premarket_volume,
                    pm_rvol=EXCLUDED.pm_rvol,
                    pm_trend_quality=EXCLUDED.pm_trend_quality,
                    premarket_score=EXCLUDED.premarket_score,
                    premarket_direction=EXCLUDED.premarket_direction,
                    premarket_confidence=EXCLUDED.premarket_confidence,
                    risk_flags_json=EXCLUDED.risk_flags_json,
                    raw_data_json=EXCLUDED.raw_data_json
            """, (
                ticker, run_date,
                result.get("premarket_gap"),
                result.get("premarket_high"),
                result.get("premarket_low"),
                result.get("premarket_volume"),
                result.get("pm_rvol"),
                result.get("pm_trend_quality"),
                result.get("premarket_score"),
                result.get("premarket_direction"),
                result.get("premarket_confidence"),
                json.dumps(result.get("premarket_risk_flags", [])),
                json.dumps({k: v for k, v in result.items()
                            if k not in ("premarket_risk_flags",)}),
            ))
            conn.commit()
    except Exception as e:
        log.warning(f"[_store_premarket] {ticker} {run_date}: {e}")
