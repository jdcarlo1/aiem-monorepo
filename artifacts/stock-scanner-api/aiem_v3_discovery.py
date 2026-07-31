"""
AIEM v3 — Phase 3: Autonomous Polygon Discovery Engine
Discovers stock candidates independently from polygon_market_daily.
Zero dependency on the stock scanner website.
"""

import os
import json
import math
from datetime import date, datetime
from typing import List, Dict, Optional

_DB_URL = os.environ.get("DATABASE_URL", "")

_MIN_PRICE        = 2.0
_MAX_PRICE        = 500.0
_MIN_VOLUME       = 150_000
_TOP_N_UNIVERSE   = 300   # candidates pulled for history scoring
_HISTORY_DAYS     = 28    # calendar days of history window

DISCOVERY_MOMENTUM     = "MOMENTUM_LEADER"
DISCOVERY_TREND        = "TREND_LEADER"
DISCOVERY_RELATIVE_STR = "RELATIVE_STRENGTH"
DISCOVERY_OVERSOLD     = "OVERSOLD_BOUNCE"
DISCOVERY_BREAKOUT     = "BREAKOUT_CANDIDATE"
DISCOVERY_GAP_CONTINUE = "GAP_CONTINUATION"
DISCOVERY_HIGH_RVOL    = "HIGH_RVOL_SETUP"


def _sf(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except Exception:
        return d


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def _sma(closes: list, period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(closes: list, period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema


def _momentum(closes: list, lookback: int) -> Optional[float]:
    if len(closes) <= lookback or closes[-(lookback + 1)] == 0:
        return None
    return (closes[-1] - closes[-(lookback + 1)]) / closes[-(lookback + 1)] * 100.0


# ── Universe loading ───────────────────────────────────────────────────────────

def load_universe(db_url: str) -> List[Dict]:
    import psycopg2
    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
            row = cur.fetchone()
            if not row or not row[0]:
                print("[v3_discovery] no polygon_market_daily data")
                return []
            latest_date = row[0]

            cur.execute("""
                SELECT ticker, close_price, open_price, high_price, low_price,
                       vwap, volume, prev_close, gap_pct, rvol,
                       close_strength, range_pct
                FROM   polygon_market_daily
                WHERE  scan_date  = %s
                  AND  close_price >= %s
                  AND  close_price <= %s
                  AND  volume      >= %s
                  AND  rvol IS NOT NULL
                  AND  rvol > 0
                ORDER  BY rvol DESC NULLS LAST
                LIMIT  2000
            """, (latest_date, _MIN_PRICE, _MAX_PRICE, _MIN_VOLUME))

            universe = []
            for r in cur.fetchall():
                ticker = r[0]
                if not ticker or len(ticker) > 5:
                    continue
                universe.append({
                    "ticker":        ticker,
                    "close":         _sf(r[1]),
                    "open":          _sf(r[2]),
                    "high":          _sf(r[3]),
                    "low":           _sf(r[4]),
                    "vwap":          _sf(r[5]),
                    "volume":        int(r[6] or 0),
                    "prev_close":    _sf(r[7]),
                    "gap_pct":       _sf(r[8]),
                    "rvol":          _sf(r[9]),
                    "close_strength":_sf(r[10], 0.5),
                    "range_pct":     _sf(r[11]),
                    "scan_date":     latest_date,
                })
            print(f"[v3_discovery] universe {len(universe)} tickers for {latest_date}")
            return universe
    except Exception as e:
        print(f"[v3_discovery] universe load error: {e}")
        return []


def load_history(db_url: str, tickers: List[str]) -> Dict[str, List[float]]:
    import psycopg2
    if not tickers:
        return {}
    try:
        with psycopg2.connect(db_url, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, close_price
                FROM   polygon_market_daily
                WHERE  ticker   = ANY(%s)
                  AND  scan_date >= (SELECT MAX(scan_date) - INTERVAL %s
                                     FROM polygon_market_daily)
                  AND  close_price > 0
                ORDER  BY ticker, scan_date ASC
            """, (tickers, f"{_HISTORY_DAYS} days"))

            history: Dict[str, List[float]] = {}
            for ticker, close_price in cur.fetchall():
                history.setdefault(ticker, []).append(_sf(close_price))
            return history
    except Exception as e:
        print(f"[v3_discovery] history load error: {e}")
        return {}


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_candidate(row: Dict, history: List[float]) -> Dict:
    ticker    = row["ticker"]
    close     = row["close"]
    rvol      = row["rvol"]
    gap_pct   = row["gap_pct"]
    close_str = row["close_strength"]
    range_pct = row["range_pct"]
    vwap      = row["vwap"]

    has_hist = len(history) >= 10
    closes   = history if has_hist else [row["prev_close"], close]

    # 1. Momentum (30 pts)
    rvol_pts      = min(15.0, rvol * 2.0)
    close_str_pts = close_str * 10.0
    gap_pts       = min(5.0, gap_pct * 0.3) if gap_pct > 0 else 0.0
    momentum_pts  = rvol_pts + close_str_pts + gap_pts

    # 2. Trend (25 pts)
    trend_pts = 0.0
    if has_hist:
        sma20 = _sma(closes, 20)
        mom5  = _momentum(closes, min(5,  len(closes) - 1))
        mom20 = _momentum(closes, min(20, len(closes) - 1))
        if sma20 and close > sma20: trend_pts += 8.0
        # Task #92 fix 2026-07-30: replaced sma50 check (was identical to sma20 with
        # _HISTORY_DAYS=28 → min(50,20)=20 bars → same average → duplicate +8pt signal).
        # Replacement: "price in upper half of 10-day range" — genuinely independent from
        # the SMA20 cross; measures trend continuation via range position rather than level.
        # Works within any window ≥2 bars; requires no extra data pull.
        if len(closes) >= 2:
            _hi10 = max(closes[-10:]) if len(closes) >= 10 else max(closes)
            _lo10 = min(closes[-10:]) if len(closes) >= 10 else min(closes)
            _mid10 = (_hi10 + _lo10) / 2.0
            if close > _mid10: trend_pts += 8.0
        if mom5  and mom5  > 0:    trend_pts += 5.0
        if mom20 and mom20 > 2.0:  trend_pts += 4.0
    else:
        trend_pts = 12.0 if gap_pct > 1.0 else 8.0

    # 3. Relative Strength (20 pts)
    rs_pts = 0.0
    if rvol >= 3.0:   rs_pts += 10.0
    elif rvol >= 2.0: rs_pts += 7.0
    elif rvol >= 1.5: rs_pts += 4.0
    if gap_pct > 3.0:   rs_pts += 10.0
    elif gap_pct > 1.0: rs_pts += 6.0
    elif gap_pct > 0.0: rs_pts += 3.0

    # 4. Oversold Bounce (15 pts)
    bounce_pts = 0.0
    rsi_val    = 50.0
    if has_hist and len(closes) >= 6:
        rsi_val    = _rsi(closes)
        recent_low = min(closes[-5:])
        peak_10    = max(closes[-10:]) if len(closes) >= 10 else closes[-1]
        drawdown   = (recent_low - peak_10) / peak_10 * 100.0 if peak_10 else 0.0
        if rsi_val < 35:   bounce_pts += 8.0
        elif rsi_val < 45: bounce_pts += 5.0
        if drawdown < -8.0 and close > closes[-2]:
            bounce_pts += 7.0
        elif drawdown < -5.0 and close_str > 0.60:
            bounce_pts += 4.0

    # 5. Breakout Setup (10 pts)
    setup_pts = 0.0
    if close_str > 0.80:   setup_pts += 5.0
    elif close_str > 0.65: setup_pts += 3.0
    if range_pct > 3.0:    setup_pts += 3.0
    elif range_pct > 1.5:  setup_pts += 1.5
    if vwap > 0 and close >= vwap: setup_pts += 2.0

    # Total
    raw   = momentum_pts + trend_pts + rs_pts + bounce_pts + setup_pts
    score = min(100.0, (raw / 100.0) * 100.0)

    # Primary type
    type_scores = {
        DISCOVERY_MOMENTUM:     momentum_pts / 30.0,
        DISCOVERY_TREND:        trend_pts    / 25.0,
        DISCOVERY_RELATIVE_STR: rs_pts       / 20.0,
        DISCOVERY_OVERSOLD:     bounce_pts   / 15.0,
        DISCOVERY_BREAKOUT:     setup_pts    / 10.0,
    }
    dtype = max(type_scores, key=type_scores.get)
    if gap_pct > 5.0 and close_str > 0.60: dtype = DISCOVERY_GAP_CONTINUE
    if rvol >= 5.0:                         dtype = DISCOVERY_MOMENTUM

    rsi_str = f" RSI={rsi_val:.0f}" if has_hist else ""
    detail  = (f"rvol={rvol:.1f}x gap={gap_pct:+.1f}% "
               f"cs={close_str:.2f} rng={range_pct:.1f}%{rsi_str}")

    return {
        "ticker":           ticker,
        "discovery_score":  round(score, 1),
        "discovery_type":   dtype,
        "detail":           detail,
        "confidence":       round(min(0.95, score / 100.0), 2),
        "component_scores": {
            "momentum": round(momentum_pts, 1),
            "trend":    round(trend_pts, 1),
            "rs":       round(rs_pts, 1),
            "bounce":   round(bounce_pts, 1),
            "setup":    round(setup_pts, 1),
        },
    }


# ── Storage ────────────────────────────────────────────────────────────────────

def store_discoveries(db_url: str, discoveries: List[Dict], session_id: str, scan_date) -> int:
    import psycopg2
    if not discoveries:
        return 0
    written = 0
    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            for d in discoveries:
                cur.execute("""
                    INSERT INTO aiem_discovery_memory
                        (discovery_date, session_id, ticker, discovery_type,
                         raw_signal, confidence, promoted_to_pick, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, FALSE, NOW())
                    ON CONFLICT DO NOTHING
                """, (
                    scan_date, session_id, d["ticker"], d["discovery_type"],
                    json.dumps({
                        "discovery_score": d["discovery_score"],
                        "detail":          d["detail"],
                        "components":      d.get("component_scores", {}),
                    }),
                    d["confidence"],
                ))
                written += 1
            conn.commit()
    except Exception as e:
        print(f"[v3_discovery] store error: {e}")
    return written


# ── Main entry points ──────────────────────────────────────────────────────────

def run_discovery(db_url: str = None, top_n: int = 30) -> List[Dict]:
    """
    Full discovery scan. Returns top_n ranked candidates.
    Each dict: ticker, discovery_score, discovery_type, detail, confidence.
    """
    db_url = db_url or _DB_URL
    print("[v3_discovery] autonomous polygon discovery starting...")

    universe = load_universe(db_url)
    if not universe:
        return []

    top_candidates = universe[:_TOP_N_UNIVERSE]
    tickers        = [c["ticker"] for c in top_candidates]
    history_map    = load_history(db_url, tickers)

    scored = []
    for row in top_candidates:
        hist   = history_map.get(row["ticker"], [])
        result = score_candidate(row, hist)
        result["scan_date"] = row["scan_date"]
        scored.append(result)

    scored.sort(key=lambda x: x["discovery_score"], reverse=True)

    session_id = f"v3_disc_{date.today().isoformat()}"
    written    = store_discoveries(db_url, scored[:100], session_id,
                                   date.today())  # store run-date, not polygon data date

    top = scored[:top_n]
    print(f"[v3_discovery] {len(scored)} scored, {written} stored, "
          f"top={top[0]['ticker'] if top else 'none'} "
          f"score={top[0]['discovery_score'] if top else 0}")
    return top


def get_todays_discoveries(db_url: str = None, min_confidence: float = 0.40) -> List[Dict]:
    """Return today's pre-computed discoveries from DB — fast, no re-scan."""
    import psycopg2
    db_url = db_url or _DB_URL
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, discovery_type, raw_signal, confidence
                FROM   aiem_discovery_memory
                WHERE  discovery_date = (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::date
                  AND  confidence >= %s
                ORDER  BY confidence DESC
                LIMIT  50
            """, (min_confidence,))
            results = []
            for ticker, dtype, raw_signal, confidence in cur.fetchall():
                try:
                    sig = json.loads(raw_signal) if raw_signal else {}
                except Exception:
                    sig = {}
                results.append({
                    "ticker":          ticker,
                    "discovery_score": sig.get("discovery_score", _sf(confidence) * 100),
                    "discovery_type":  dtype,
                    "detail":          sig.get("detail", dtype),
                    "confidence":      _sf(confidence),
                })
            return results
    except Exception as e:
        print(f"[v3_discovery] get_todays_discoveries error: {e}")
        return []
