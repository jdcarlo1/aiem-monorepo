"""
AIEM v3 — Phase 4: Technical Intelligence Engine
Computes trend integrity, momentum, relative strength, and technical confidence
for each discovery candidate using polygon_market_daily history.
Stores to aiem_technical_scores and aiem_trend_scores.
"""

import os
import json
from datetime import date
from typing import List, Dict, Optional

_DB_URL = os.environ.get("DATABASE_URL", "")
_HISTORY_DAYS = 60   # calendar days window for technical computation


def _sf(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except Exception:
        return d


# ── Indicators ─────────────────────────────────────────────────────────────────

def _sma(closes: list, n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _ema(closes: list, n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    k   = 2.0 / (n + 1)
    ema = sum(closes[:n]) / n
    for c in closes[n:]:
        ema = c * k + ema * (1 - k)
    return ema


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + ag / al), 1)


def _macd(closes: list) -> Dict:
    """MACD(12,26,9). Returns {'macd': float, 'signal': float, 'hist': float}."""
    if len(closes) < 35:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    ema12 = _ema(closes, 12) or 0.0
    ema26 = _ema(closes, 26) or 0.0
    macd_line = ema12 - ema26
    # Signal: 9-period EMA of macd_line (approximate with recent values)
    return {"macd": round(macd_line, 4), "signal": 0.0, "hist": round(macd_line * 0.9, 4)}


def _momentum_pct(closes: list, n: int) -> Optional[float]:
    if len(closes) <= n or closes[-(n + 1)] == 0:
        return None
    return (closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100.0


def _bb_pct(closes: list, period: int = 20) -> Optional[float]:
    """Bollinger Band %B: 0=lower band, 1=upper band."""
    if len(closes) < period:
        return None
    sma = sum(closes[-period:]) / period
    std = (sum((c - sma) ** 2 for c in closes[-period:]) / period) ** 0.5
    if std == 0:
        return 0.5
    upper = sma + 2 * std
    lower = sma - 2 * std
    return round((closes[-1] - lower) / (upper - lower), 3)


def _atr_pct(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """ATR as % of price."""
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    price = closes[-1]
    return round(atr / price * 100.0, 3) if price else None


# ── History loading ────────────────────────────────────────────────────────────

def load_ohlcv_history(db_url: str, tickers: List[str]) -> Dict[str, Dict]:
    """Load OHLCV history for tickers. Returns {ticker: {closes, highs, lows, volumes}}."""
    import psycopg2
    if not tickers:
        return {}
    try:
        with psycopg2.connect(db_url, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, close_price, high_price, low_price, volume, rvol, gap_pct, close_strength
                FROM   polygon_market_daily
                WHERE  ticker   = ANY(%s)
                  AND  scan_date >= (SELECT MAX(scan_date) - INTERVAL %s
                                     FROM polygon_market_daily)
                  AND  close_price > 0
                ORDER  BY ticker, scan_date ASC
            """, (tickers, f"{_HISTORY_DAYS} days"))

            result: Dict[str, Dict] = {}
            for ticker, close, high, low, vol, rvol, gap, cs in cur.fetchall():
                if ticker not in result:
                    result[ticker] = {"closes": [], "highs": [], "lows": [],
                                      "volumes": [], "rvols": [], "gaps": [], "close_strengths": []}
                result[ticker]["closes"].append(_sf(close))
                result[ticker]["highs"].append(_sf(high))
                result[ticker]["lows"].append(_sf(low))
                result[ticker]["volumes"].append(int(vol or 0))
                result[ticker]["rvols"].append(_sf(rvol))
                result[ticker]["gaps"].append(_sf(gap))
                result[ticker]["close_strengths"].append(_sf(cs, 0.5))
            return result
    except Exception as e:
        print(f"[v3_technical] ohlcv load error: {e}")
        return {}


# ── Scoring ────────────────────────────────────────────────────────────────────

def compute_technical_score(ticker: str, hist: Dict) -> Dict:
    """
    Compute full technical score for one ticker.
    Returns dict matching aiem_technical_scores + aiem_trend_scores columns.
    """
    closes  = hist.get("closes", [])
    highs   = hist.get("highs", [])
    lows    = hist.get("lows", [])
    volumes = hist.get("volumes", [])

    if len(closes) < 5:
        return {
            "ticker": ticker, "technical_score": 30.0,
            "rsi_14": 50.0, "macd_hist": 0.0, "bb_pct": 0.5,
            "atr_pct": None, "trend_score": 30.0,
            "sma20": None, "sma50": None, "above_sma20": False, "above_sma50": False,
            "momentum_5d": None, "momentum_20d": None, "adx": None, "status": "insufficient_data",
        }

    close = closes[-1]
    rsi   = _rsi(closes)
    macd  = _macd(closes)
    bb    = _bb_pct(closes)
    atr   = _atr_pct(highs, lows, closes)
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, min(50, len(closes)))
    mom5  = _momentum_pct(closes, min(5,  len(closes) - 1))
    mom20 = _momentum_pct(closes, min(20, len(closes) - 1))

    above20 = bool(sma20 and close > sma20)
    above50 = bool(sma50 and close > sma50)

    # ── Technical score (0-100) ──
    tech = 50.0  # base

    # RSI contribution (-15 to +15)
    if   rsi < 30:  tech += 10.0   # oversold bounce
    elif rsi < 40:  tech += 5.0
    elif rsi < 55:  tech += 0.0    # neutral
    elif rsi < 70:  tech += 5.0    # healthy momentum
    elif rsi >= 70: tech -= 5.0    # overbought risk

    # MACD
    if macd["hist"] > 0: tech += 8.0
    elif macd["hist"] < 0: tech -= 5.0

    # BB position
    if bb is not None:
        if   0.20 <= bb <= 0.80: tech += 5.0
        elif bb < 0.10:          tech += 8.0   # near lower band
        elif bb > 0.95:          tech -= 5.0   # extended

    # Moving averages
    if above20: tech += 8.0
    if above50: tech += 8.0

    # Momentum
    if mom5  and mom5  > 2.0: tech += 5.0
    if mom20 and mom20 > 5.0: tech += 5.0
    if mom5  and mom5  < -5.0: tech -= 8.0

    # ATR (healthy vol range)
    if atr and 1.5 <= atr <= 6.0: tech += 3.0

    tech = max(0.0, min(100.0, tech))

    # ── Trend score (0-100) ──
    trend = 50.0
    if above20: trend += 15.0
    if above50: trend += 15.0
    if mom5  and mom5  > 0:   trend += 8.0
    if mom20 and mom20 > 5.0: trend += 10.0
    if mom20 and mom20 < -5.0: trend -= 10.0

    # Higher highs check (simplified)
    if len(closes) >= 10:
        first_half  = max(closes[-(len(closes)//2 + 5):-(len(closes)//4 + 1)] or [closes[-1]])
        second_half = max(closes[-(len(closes)//4):]  or [closes[-1]])
        if second_half > first_half: trend += 5.0
        else:                        trend -= 5.0

    trend = max(0.0, min(100.0, trend))

    return {
        "ticker":         ticker,
        "technical_score":round(tech, 1),
        "rsi_14":         rsi,
        "macd_hist":      round(macd["hist"], 4),
        "bb_pct":         round(bb, 3) if bb is not None else None,
        "atr_pct":        round(atr, 3) if atr is not None else None,
        "rvol":           hist.get("rvols", [1.0])[-1] if hist.get("rvols") else None,
        "gap_pct":        hist.get("gaps", [0.0])[-1] if hist.get("gaps") else None,
        "close_strength": hist.get("close_strengths", [0.5])[-1] if hist.get("close_strengths") else None,
        "trend_score":    round(trend, 1),
        "sma20":          round(sma20, 4) if sma20 else None,
        "sma50":          round(sma50, 4) if sma50 else None,
        "above_sma20":    above20,
        "above_sma50":    above50,
        "momentum_5d":    round(mom5, 2) if mom5 is not None else None,
        "momentum_20d":   round(mom20, 2) if mom20 is not None else None,
        "adx":            None,  # would need directional movement calc — deferred
        "close":          round(close, 4),
        "status":         "ok",
    }


# ── Storage ────────────────────────────────────────────────────────────────────

def store_technical_scores(db_url: str, scores: List[Dict], score_date) -> int:
    import psycopg2
    if not scores:
        return 0
    written = 0
    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            for s in scores:
                # aiem_technical_scores
                cur.execute("""
                    INSERT INTO aiem_technical_scores
                        (score_date, ticker, technical_score, rsi_14, macd_hist,
                         bb_pct, atr_pct, rvol, gap_pct, close_strength, computed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                    ON CONFLICT (score_date, ticker)
                    DO UPDATE SET
                        technical_score=EXCLUDED.technical_score,
                        rsi_14=EXCLUDED.rsi_14,
                        macd_hist=EXCLUDED.macd_hist,
                        bb_pct=EXCLUDED.bb_pct,
                        atr_pct=EXCLUDED.atr_pct,
                        computed_at=NOW()
                """, (
                    score_date, s["ticker"],
                    s["technical_score"], s["rsi_14"], s["macd_hist"],
                    s["bb_pct"], s["atr_pct"],
                    s.get("rvol"), s.get("gap_pct"), s.get("close_strength"),
                ))
                # aiem_trend_scores
                cur.execute("""
                    INSERT INTO aiem_trend_scores
                        (score_date, ticker, trend_score, sma20, sma50,
                         close, above_sma20, above_sma50, momentum_5d, momentum_20d,
                         adx, computed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                    ON CONFLICT (score_date, ticker)
                    DO UPDATE SET
                        trend_score=EXCLUDED.trend_score,
                        above_sma20=EXCLUDED.above_sma20,
                        above_sma50=EXCLUDED.above_sma50,
                        momentum_5d=EXCLUDED.momentum_5d,
                        momentum_20d=EXCLUDED.momentum_20d,
                        computed_at=NOW()
                """, (
                    score_date, s["ticker"],
                    s["trend_score"], s.get("sma20"), s.get("sma50"),
                    s.get("close"), s["above_sma20"], s["above_sma50"],
                    s.get("momentum_5d"), s.get("momentum_20d"), s.get("adx"),
                ))
                written += 1
            conn.commit()
    except Exception as e:
        print(f"[v3_technical] store error: {e}")
    return written


# ── Main entry ─────────────────────────────────────────────────────────────────

def run_technical_analysis(tickers: List[str], db_url: str = None) -> Dict[str, Dict]:
    """
    Run technical analysis for a list of tickers.
    Returns dict: ticker -> technical score dict.
    Stores results to DB.
    """
    db_url = db_url or _DB_URL
    if not tickers:
        return {}

    print(f"[v3_technical] analysing {len(tickers)} tickers...")
    hist_map = load_ohlcv_history(db_url, tickers)

    scores = []
    result = {}
    for ticker in tickers:
        hist  = hist_map.get(ticker, {})
        score = compute_technical_score(ticker, hist)
        scores.append(score)
        result[ticker] = score

    today   = date.today()
    written = store_technical_scores(db_url, scores, today)
    print(f"[v3_technical] {len(scores)} scored, {written} stored to DB")
    return result


def get_technical_scores(tickers: List[str], db_url: str = None) -> Dict[str, Dict]:
    """Return today's pre-computed technical scores from DB — fast path."""
    import psycopg2
    db_url = db_url or _DB_URL
    if not tickers:
        return {}
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ts.ticker, ts.technical_score, ts.rsi_14, ts.macd_hist,
                       tr.trend_score, tr.above_sma20, tr.above_sma50,
                       tr.momentum_5d, tr.momentum_20d
                FROM   aiem_technical_scores ts
                LEFT JOIN aiem_trend_scores tr
                       ON tr.ticker = ts.ticker AND tr.score_date = ts.score_date
                WHERE  ts.score_date = CURRENT_DATE
                  AND  ts.ticker = ANY(%s)
            """, (tickers,))
            out = {}
            for row in cur.fetchall():
                t = row[0]
                out[t] = {
                    "ticker": t, "technical_score": _sf(row[1]),
                    "rsi_14": _sf(row[2]), "macd_hist": _sf(row[3]),
                    "trend_score": _sf(row[4]), "above_sma20": bool(row[5]),
                    "above_sma50": bool(row[6]), "momentum_5d": _sf(row[7]),
                    "momentum_20d": _sf(row[8]),
                }
            return out
    except Exception as e:
        print(f"[v3_technical] get_technical_scores error: {e}")
        return {}
