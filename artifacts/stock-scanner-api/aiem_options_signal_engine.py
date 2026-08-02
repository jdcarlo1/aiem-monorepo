"""
aiem_options_signal_engine.py — Phase 2: Options Engine Signal Computation Layer

Produces a SignalResult for each candidate ticker. This is a pure read-only
computation module: no DB writes (except optional pattern snapshot persistence),
no paper-trade decisions, no position management.

Phase 3+ handles strategy selection; Phase 4+ handles execution.

Data sources (all read-only unless persist_snapshot=True):
  polygon_market_daily    — OHLCV bars for all technical + quant indicators
  polygon_rvol_scan       — RVOL ratio + gap_pct (premarket proxy)
  sector_etf_daily        — sector relative strength
  garch_regime_log / regime_history  — market regime
  aiem_multitimeframe     — MTF alignment (via Polygon API)
  aiem_pattern_engine     — pattern detection (repaired Phase 2)

Mandatory gate: if polygon_rvol_scan data is MISSING for the ticker
(not present within _STALE_DAYS), thesis = NO_TRADE, blocking_reason set.
"""
from __future__ import annotations

import os
import sys
import math
import logging
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

import numpy as np
import pandas as pd
import psycopg2

log = logging.getLogger("aiem.options.signal_engine")

# Bootstrap sys.path so sibling imports work when run from any cwd
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_DB_URL = os.environ.get("DATABASE_URL", "")

# ── Module status codes ───────────────────────────────────────────────────────
AVAILABLE      = "AVAILABLE"
STALE          = "STALE"
MISSING        = "MISSING"
FAILED         = "FAILED"
NOT_APPLICABLE = "NOT_APPLICABLE"

# ── Direction / thesis codes ──────────────────────────────────────────────────
BULLISH  = "BULLISH"
BEARISH  = "BEARISH"
NEUTRAL  = "NEUTRAL"
NO_TRADE = "NO_TRADE"

# ── Signal quality codes ──────────────────────────────────────────────────────
STRONG       = "STRONG"
MODERATE     = "MODERATE"
WEAK         = "WEAK"
INSUFFICIENT = "INSUFFICIENT"

# ── Thresholds (all named constants — no magic numbers in logic) ──────────────
_MIN_BARS_TECHNICAL   = 22    # RSI(14) + MACD(26) need at least 22 for init pass
_MIN_BARS_QUANT       = 45    # Hurst RS/analysis needs enough segments
_MIN_BARS_GARCH       = 60    # GARCH(1,1) fit degrades under 60 returns
_STALE_DAYS           = 3     # rvol_scan data older than this = STALE
_RSI_OVERSOLD         = 35    # RSI below this = oversold → bullish vote
_RSI_OVERBOUGHT       = 68    # RSI above this = overbought → bearish vote
_ADX_TRENDING         = 25    # ADX above this = trending market
_BB_LOWER_ZONE        = 0.20  # bb_position below this = near lower band
_BB_UPPER_ZONE        = 0.80  # bb_position above this = near upper band
_PREMARKET_GAP_BULL   =  1.5  # gap_pct above this → bullish premarket evidence
_PREMARKET_GAP_BEAR   = -1.5  # gap_pct below this → bearish premarket evidence
_RVOL_HIGH            =  2.0  # RVOL above this = high activity
_HURST_TRENDING       =  0.58 # Hurst > this = trend-persistent
_HURST_MEANREV        =  0.42 # Hurst < this = mean-reverting
_VPIN_HIGH_TOXICITY   =  0.65 # VPIN above this = toxic order flow
_MIN_CONFIRMING_SIGS  =  2    # need at least 2 same-direction votes for WEAK
_MIN_CONFIDENCE       =  0.22 # (bull - bear) / total must exceed this for directional
_SECTOR_STALE_DAYS    =  5    # sector_etf_daily data older than this = STALE


# ── SignalResult frozen dataclass ─────────────────────────────────────────────
@dataclass(frozen=True)
class SignalResult:
    """
    Immutable result of the signal engine for one ticker.

    Every _status field uses one of: AVAILABLE | STALE | MISSING | FAILED | NOT_APPLICABLE.
    None-valued component fields mean the module was unavailable, not that the
    value was zero — callers must inspect the corresponding _status field.
    """
    # ── Core ────────────────────────────────────────────────────────────────
    ticker:          str
    computed_at:     str            # ISO UTC
    thesis:          str            # BULLISH | BEARISH | NEUTRAL | NO_TRADE
    signal_quality:  str            # STRONG | MODERATE | WEAK | INSUFFICIENT
    confidence:      float          # 0.0–1.0; fraction of votes in winning direction
    blocking_reason: Optional[str]  # set when thesis=NO_TRADE

    # ── VWAP ────────────────────────────────────────────────────────────────
    vwap_value:         Optional[float]   # rolling VWAP from daily OHLCV (proxy)
    vwap_pct_deviation: Optional[float]   # (close - vwap) / vwap * 100
    vwap_reclaim:       Optional[bool]    # close > VWAP after prev close < VWAP
    vwap_status:        str

    # ── EMA / SMA ────────────────────────────────────────────────────────────
    ema_9:              Optional[float]
    ema_20:             Optional[float]
    ema_50:             Optional[float]
    sma_50:             Optional[float]
    sma_200:            Optional[float]
    price_vs_ema20_pct: Optional[float]   # (close - ema20) / ema20 * 100
    ema_status:         str

    # ── ADX ──────────────────────────────────────────────────────────────────
    adx:         Optional[float]    # 0–100; >25 = trending
    adx_di_plus: Optional[float]    # +DI (bullish pressure)
    adx_di_minus: Optional[float]   # -DI (bearish pressure)
    adx_trend:   Optional[str]      # TRENDING | RANGING
    adx_status:  str

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi:        Optional[float]     # 0–100
    rsi_signal: Optional[str]       # OVERBOUGHT | OVERSOLD | NEUTRAL
    rsi_status: str

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_line:        Optional[float]
    macd_signal_line: Optional[float]
    macd_hist:        Optional[float]
    macd_cross:       Optional[str]   # BULLISH_CROSS | BEARISH_CROSS | NONE
    macd_status:      str

    # ── ATR ──────────────────────────────────────────────────────────────────
    atr:       Optional[float]      # 14-period ATR, absolute
    atr_pct:   Optional[float]      # ATR / close * 100
    atr_status: str

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_upper:    Optional[float]
    bb_mid:      Optional[float]    # 20-period SMA
    bb_lower:    Optional[float]
    bb_position: Optional[float]    # 0 = at/below lower, 1 = at/above upper
    bb_squeeze:  Optional[bool]     # bandwidth < 5% of mid
    bb_status:   str

    # ── RVOL (mandatory — from polygon_rvol_scan) ─────────────────────────────
    rvol:        Optional[float]    # RVOL ratio from polygon scan
    rvol_status: str                # AVAILABLE | STALE | MISSING

    # ── Volume Profile ────────────────────────────────────────────────────────
    avg_volume_20d: Optional[float]
    volume_trend:   Optional[str]   # INCREASING | DECREASING | FLAT
    vol_climax:     Optional[bool]  # last bar volume > 3× 20d avg
    volume_status:  str

    # ── Support / Resistance ─────────────────────────────────────────────────
    nearest_support:      Optional[float]
    nearest_resistance:   Optional[float]
    price_vs_support_pct: Optional[float]   # % above nearest support
    price_vs_resist_pct:  Optional[float]   # % below nearest resistance
    sr_status:            str

    # ── GARCH ────────────────────────────────────────────────────────────────
    garch_forecast_vol: Optional[float]   # next 5-day daily vol forecast (%)
    garch_persistence:  Optional[float]   # alpha+beta; ≥1.0 = non-stationary
    garch_regime_vote:  Optional[int]     # -1 | 0 | 1 from garch_regime_indicator
    garch_reason:       Optional[str]     # human-readable explanation
    garch_status:       str

    # ── VPIN ─────────────────────────────────────────────────────────────────
    vpin_score:  Optional[float]    # 0–1; last bucket; high = toxic order flow
    vpin_signal: Optional[str]      # HIGH_TOXICITY | LOW_TOXICITY | NEUTRAL
    vpin_status: str

    # ── Hurst Exponent ────────────────────────────────────────────────────────
    hurst_exponent_val: Optional[float]   # 0–1
    hurst_regime:       Optional[str]     # TRENDING | MEAN_REVERTING | RANDOM
    hurst_status:       str

    # ── Patterns ─────────────────────────────────────────────────────────────
    pattern_score:          Optional[float]   # None = FAILED or no PASS patterns
    pattern_direction:      Optional[str]     # BULLISH | BEARISH | NEUTRAL
    pattern_confidence:     Optional[float]   # 0–1; best pattern confidence
    pattern_name:           Optional[str]     # primary pattern name
    pattern_timeframe:      Optional[str]     # "daily"
    pattern_invalidation:   Optional[float]   # price level that invalidates
    pattern_families_fired: Optional[str]     # comma-sep families with detections
    pattern_status:         str               # AVAILABLE | FAILED | NO_DATA | INSUFFICIENT_BARS

    # ── Premarket (from polygon_rvol_scan) ────────────────────────────────────
    premarket_gap_pct:      Optional[float]   # gap_pct from rvol scan
    premarket_volume_ratio: Optional[float]   # rvol as premarket vol proxy
    premarket_direction:    Optional[str]     # GAP_UP | GAP_DOWN | FLAT
    premarket_scan_date:    Optional[str]     # ISO date of the source rvol row
    premarket_status:       str

    # ── MTF Alignment ─────────────────────────────────────────────────────────
    mtf_alignment_score: Optional[float]    # 0–1 weighted alignment
    mtf_dominant_bias:   Optional[str]      # BULLISH | BEARISH | NEUTRAL
    mtf_bull_tf_count:   Optional[int]
    mtf_bear_tf_count:   Optional[int]
    mtf_conflict_score:  Optional[float]    # 0–1; high = disagreement
    mtf_entry_timing:    Optional[str]      # READY | WAIT | CONFLICTED | INSUFFICIENT_DATA
    mtf_status:          str

    # ── Sector Relative Strength ──────────────────────────────────────────────
    sector_etf:                   Optional[str]    # e.g. XLK
    sector_5d_return_pct:         Optional[float]  # sector ETF 5-day return
    spy_5d_return_pct:            Optional[float]  # SPY 5-day return
    sector_relative_strength_pct: Optional[float]  # sector - SPY return
    breadth_status:               str

    # ── Market Regime ─────────────────────────────────────────────────────────
    regime:        Optional[str]    # BULL | BEAR | NEUTRAL | VOLATILE
    regime_source: Optional[str]    # garch_regime_log | regime_history | GARCH_INLINE
    regime_status: str

    # ── Evidence chain ────────────────────────────────────────────────────────
    bullish_evidence: tuple   # tuple of str labels
    bearish_evidence: tuple
    neutral_evidence: tuple
    failed_modules:   tuple   # tuple of "module:reason" strings


# ── Internal bar fetch ────────────────────────────────────────────────────────

def _fetch_bars(ticker: str, lookback: int = 120) -> List[Dict]:
    """
    Pull OHLCV bars from polygon_market_daily, oldest-first.
    Returns list of {"date", "open", "high", "low", "close", "volume"} dicts.
    Never raises — returns [] on any error.
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT scan_date, open_price, high_price, low_price,
                           close_price, COALESCE(volume, 0)
                    FROM polygon_market_daily
                    WHERE ticker = %s
                      AND open_price IS NOT NULL
                      AND close_price IS NOT NULL
                    ORDER BY scan_date DESC
                    LIMIT %s
                """, (ticker.upper(), lookback))
                rows = cur.fetchall()
        return [
            {"date": str(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in reversed(rows)
        ]
    except Exception as e:
        log.warning(f"[signal_engine] _fetch_bars {ticker}: {e}")
        return []


# ── Pure-math indicator helpers ───────────────────────────────────────────────

def _ema_series(prices: List[float], n: int) -> List[float]:
    """Compute full EMA series, seeded at prices[0]. len(prices) must be >= n."""
    k = 2.0 / (n + 1)
    out = [prices[0]]
    for p in prices[1:]:
        out.append(p * k + out[-1] * (1 - k))
    return out


def _wilder_smooth(series: List[float], n: int) -> List[float]:
    """Wilder's smoothing (used in ADX/ATR): alpha = 1/n."""
    out = [sum(series[:n]) / n]
    for v in series[n:]:
        out.append(out[-1] * (n - 1) / n + v / n)
    return out


def _compute_vwap(bars: List[Dict]) -> tuple:
    """
    Daily VWAP proxy: cumulative (typical_price × volume) / cumulative_volume.
    Uses last 20 bars for the rolling window.  Returns (vwap, pct_dev, reclaim, status).
    """
    try:
        window = bars[-20:] if len(bars) >= 20 else bars
        cum_vol = 0.0
        cum_tp_vol = 0.0
        for b in window:
            tp = (b["high"] + b["low"] + b["close"]) / 3.0
            vol = b["volume"]
            cum_tp_vol += tp * vol
            cum_vol += vol
        if cum_vol < 1e-6:
            return None, None, None, MISSING
        vwap = cum_tp_vol / cum_vol
        last_close = bars[-1]["close"]
        pct_dev = (last_close - vwap) / vwap * 100.0
        # Reclaim: previous close < VWAP and current close > VWAP
        reclaim = None
        if len(bars) >= 2:
            prev_close = bars[-2]["close"]
            reclaim = bool(prev_close < vwap and last_close >= vwap)
        return round(vwap, 4), round(pct_dev, 3), reclaim, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] vwap: {e}")
        return None, None, None, FAILED


def _compute_ema_sma(closes: List[float]) -> tuple:
    """Returns (ema9, ema20, ema50, sma50, sma200, price_vs_ema20_pct, status)."""
    try:
        price = closes[-1]
        ema9  = _ema_series(closes, 9)[-1]  if len(closes) >= 9   else None
        ema20 = _ema_series(closes, 20)[-1] if len(closes) >= 20  else None
        ema50 = _ema_series(closes, 50)[-1] if len(closes) >= 50  else None
        sma50 = sum(closes[-50:]) / 50.0    if len(closes) >= 50  else None
        sma200= sum(closes[-200:]) / 200.0  if len(closes) >= 200 else None
        vs_ema20 = None
        if ema20 is not None and ema20 > 0:
            vs_ema20 = round((price - ema20) / ema20 * 100.0, 3)
        status = AVAILABLE if ema20 is not None else MISSING
        return (
            round(ema9, 4)  if ema9  is not None else None,
            round(ema20, 4) if ema20 is not None else None,
            round(ema50, 4) if ema50 is not None else None,
            round(sma50, 4) if sma50 is not None else None,
            round(sma200, 4) if sma200 is not None else None,
            vs_ema20, status,
        )
    except Exception as e:
        log.warning(f"[signal_engine] ema_sma: {e}")
        return None, None, None, None, None, None, FAILED


def _compute_adx(bars: List[Dict], period: int = 14) -> tuple:
    """
    Wilder ADX.  Returns (adx, di_plus, di_minus, trend_label, status).
    Needs at least period*2 + 2 bars.
    """
    try:
        needed = period * 2 + 2
        if len(bars) < needed:
            return None, None, None, None, MISSING
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        closes = [b["close"] for b in bars]

        tr_list, pdm_list, ndm_list = [], [], []
        for i in range(1, len(bars)):
            h, l, pc = highs[i], lows[i], closes[i - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_list.append(tr)
            up_move   = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            pdm = up_move   if up_move   > down_move and up_move   > 0 else 0.0
            ndm = down_move if down_move > up_move   and down_move > 0 else 0.0
            pdm_list.append(pdm)
            ndm_list.append(ndm)

        atr14  = _wilder_smooth(tr_list,  period)
        pdm14  = _wilder_smooth(pdm_list, period)
        ndm14  = _wilder_smooth(ndm_list, period)

        di_plus_series  = [100.0 * p / a if a > 1e-9 else 0.0 for p, a in zip(pdm14, atr14)]
        di_minus_series = [100.0 * n / a if a > 1e-9 else 0.0 for n, a in zip(ndm14, atr14)]
        dx_series = []
        for p, n in zip(di_plus_series, di_minus_series):
            denom = p + n
            dx_series.append(100.0 * abs(p - n) / denom if denom > 1e-9 else 0.0)

        adx_series = _wilder_smooth(dx_series, period)
        adx_val    = adx_series[-1]
        dip_val    = di_plus_series[-1]
        dim_val    = di_minus_series[-1]
        trend      = "TRENDING" if adx_val > _ADX_TRENDING else "RANGING"
        return round(adx_val, 2), round(dip_val, 2), round(dim_val, 2), trend, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] adx: {e}")
        return None, None, None, None, FAILED


def _compute_rsi(closes: List[float], period: int = 14) -> tuple:
    """Wilder RSI.  Returns (rsi, signal_label, status)."""
    try:
        if len(closes) < period + 1:
            return None, None, MISSING
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [max(0.0, d) for d in deltas]
        losses = [abs(min(0.0, d)) for d in deltas]
        # Wilder smoothing
        ag = _wilder_smooth(gains,  period)[-1]
        al = _wilder_smooth(losses, period)[-1]
        if al < 1e-10:
            rsi_val = 100.0
        else:
            rs = ag / al
            rsi_val = 100.0 - 100.0 / (1.0 + rs)
        if rsi_val < _RSI_OVERSOLD:
            sig = "OVERSOLD"
        elif rsi_val > _RSI_OVERBOUGHT:
            sig = "OVERBOUGHT"
        else:
            sig = "NEUTRAL"
        return round(rsi_val, 2), sig, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] rsi: {e}")
        return None, None, FAILED


def _compute_macd(closes: List[float], fast: int = 12, slow: int = 26, signal_n: int = 9) -> tuple:
    """Returns (macd_line, signal_line, hist, cross_label, status)."""
    try:
        needed = slow + signal_n + 1
        if len(closes) < needed:
            return None, None, None, None, MISSING
        ema_f = _ema_series(closes, fast)
        ema_s = _ema_series(closes, slow)
        macd_series   = [f - s for f, s in zip(ema_f, ema_s)]
        signal_series = _ema_series(macd_series, signal_n)
        hist_series   = [m - s for m, s in zip(macd_series, signal_series)]
        m  = macd_series[-1]
        sl = signal_series[-1]
        h  = hist_series[-1]
        cross = "NONE"
        if len(hist_series) >= 2:
            h_prev = hist_series[-2]
            if h_prev <= 0 and h > 0:
                cross = "BULLISH_CROSS"
            elif h_prev >= 0 and h < 0:
                cross = "BEARISH_CROSS"
        return round(m, 4), round(sl, 4), round(h, 4), cross, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] macd: {e}")
        return None, None, None, None, FAILED


def _compute_atr(bars: List[Dict], period: int = 14) -> tuple:
    """Wilder ATR.  Returns (atr, atr_pct, status)."""
    try:
        if len(bars) < period + 1:
            return None, None, MISSING
        tr_list = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr_val = _wilder_smooth(tr_list, period)[-1]
        close   = bars[-1]["close"]
        atr_pct = atr_val / close * 100.0 if close > 1e-6 else None
        return round(atr_val, 4), round(atr_pct, 3) if atr_pct else None, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] atr: {e}")
        return None, None, FAILED


def _compute_bollinger(closes: List[float], period: int = 20, nstd: float = 2.0) -> tuple:
    """Returns (upper, mid, lower, position, squeeze, status)."""
    try:
        if len(closes) < period:
            return None, None, None, None, None, MISSING
        window = closes[-period:]
        mid    = sum(window) / period
        std    = float(np.std(window, ddof=1))
        upper  = mid + nstd * std
        lower  = mid - nstd * std
        price  = closes[-1]
        rng    = upper - lower
        pos    = (price - lower) / rng if rng > 1e-10 else 0.5
        pos    = max(0.0, min(1.0, pos))
        squeeze = bool(rng / mid < 0.05) if mid > 1e-6 else False
        return round(upper, 4), round(mid, 4), round(lower, 4), round(pos, 4), squeeze, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] bollinger: {e}")
        return None, None, None, None, None, FAILED


def _compute_volume_profile(bars: List[Dict]) -> tuple:
    """Returns (avg_20d, trend, vol_climax, status)."""
    try:
        vols = [b["volume"] for b in bars]
        if len(vols) < 5:
            return None, None, None, MISSING
        window = vols[-20:] if len(vols) >= 20 else vols
        avg = sum(window) / len(window)
        last_vol = vols[-1]
        climax = bool(last_vol > avg * 3.0)
        # Trend: compare last 5 avg vs prior 5 avg
        if len(vols) >= 10:
            recent = sum(vols[-5:]) / 5.0
            prior  = sum(vols[-10:-5]) / 5.0
            if recent > prior * 1.15:
                trend = "INCREASING"
            elif recent < prior * 0.85:
                trend = "DECREASING"
            else:
                trend = "FLAT"
        else:
            trend = "FLAT"
        return round(avg, 0), trend, climax, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] volume_profile: {e}")
        return None, None, None, FAILED


def _compute_sr(bars: List[Dict]) -> tuple:
    """
    Support / resistance from recent swing highs/lows.
    Returns (nearest_support, nearest_resistance, vs_support_pct, vs_resist_pct, status).
    """
    try:
        from price_structure_patterns import (
            find_swing_points, compute_support_resistance_zones, _atr
        )
        if len(bars) < 15:
            return None, None, None, None, MISSING
        highs  = np.array([b["high"]  for b in bars], dtype=float)
        lows   = np.array([b["low"]   for b in bars], dtype=float)
        closes = np.array([b["close"] for b in bars], dtype=float)
        atr_v  = _atr(highs, lows, closes)
        sh, sl, _ = find_swing_points(highs, lows, closes, atr_val=atr_v)
        if (not sh or len(sh) == 0) and (not sl or len(sl) == 0):
            return None, None, None, None, MISSING
        price = float(closes[-1])
        zones = compute_support_resistance_zones(sh, sl, float(atr_v), price, len(bars))
        supports = [z["level"] for z in zones if z.get("type") == "support" and z["level"] < price]
        resists  = [z["level"] for z in zones if z.get("type") == "resistance" and z["level"] > price]
        sup  = max(supports) if supports else None
        res  = min(resists)  if resists  else None
        vs_sup = round((price - sup) / sup * 100.0, 2) if sup and sup > 0 else None
        vs_res = round((res - price) / price * 100.0, 2) if res and res > 0 else None
        return (
            round(sup, 4) if sup else None,
            round(res, 4) if res else None,
            vs_sup, vs_res, AVAILABLE
        )
    except Exception as e:
        log.warning(f"[signal_engine] sr: {e}")
        return None, None, None, None, FAILED


def _compute_garch(closes: List[float]) -> tuple:
    """
    GARCH(1,1) regime indicator.
    Returns (forecast_vol, persistence, vote, reason, status).
    Source: volatility_clustering.garch_regime_indicator
    """
    try:
        if len(closes) < _MIN_BARS_GARCH:
            return None, None, None, f"insufficient bars ({len(closes)} < {_MIN_BARS_GARCH})", MISSING
        from volatility_clustering import garch_regime_indicator, fit_garch_model, forecast_volatility, get_persistence
        df = pd.DataFrame({"close": closes})
        result = garch_regime_indicator(df, lookback=min(252, len(closes)))
        vote   = result.get("vote", 0)
        reason = result.get("reason", "")
        # Also extract numeric forecast and persistence for SignalResult fields
        rets = pd.Series(closes).pct_change().dropna()
        fitted = fit_garch_model(rets)
        fcast_vol = None
        persistence = None
        if fitted is not None:
            fv = forecast_volatility(fitted, horizon=5)
            if fv:
                fcast_vol = round(float(np.mean(fv["daily_vol_forecast_pct"])), 3)
            p = get_persistence(fitted)
            if p is not None:
                persistence = round(float(p), 4)
        return fcast_vol, persistence, int(vote), reason, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] garch: {e}")
        return None, None, None, str(e), FAILED


def _compute_vpin(bars: List[Dict]) -> tuple:
    """
    VPIN from advanced_quant_indicators.
    Returns (last_vpin_score, signal, status).
    """
    try:
        if len(bars) < _MIN_BARS_QUANT:
            return None, None, MISSING
        from advanced_quant_indicators import vpin as compute_vpin
        vols   = pd.Series([b["volume"] for b in bars])
        prices = pd.Series([b["close"]  for b in bars])
        vpin_s = compute_vpin(vols, prices)
        last   = float(vpin_s.dropna().iloc[-1]) if not vpin_s.dropna().empty else None
        if last is None:
            return None, None, MISSING
        sig = "HIGH_TOXICITY" if last > _VPIN_HIGH_TOXICITY else ("LOW_TOXICITY" if last < 0.35 else "NEUTRAL")
        return round(last, 4), sig, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] vpin: {e}")
        return None, None, FAILED


def _compute_hurst(closes: List[float]) -> tuple:
    """
    Hurst exponent from advanced_quant_indicators.
    Returns (hurst, regime_label, status).
    """
    try:
        if len(closes) < _MIN_BARS_QUANT:
            return None, None, MISSING
        from advanced_quant_indicators import hurst_exponent
        h = hurst_exponent(pd.Series(closes))
        if h < _HURST_MEANREV:
            regime = "MEAN_REVERTING"
        elif h > _HURST_TRENDING:
            regime = "TRENDING"
        else:
            regime = "RANDOM"
        return round(float(h), 4), regime, AVAILABLE
    except Exception as e:
        log.warning(f"[signal_engine] hurst: {e}")
        return None, None, FAILED


def _compute_premarket(ticker: str) -> tuple:
    """
    Pull premarket signal from polygon_rvol_scan.
    gap_pct  = premarket gap proxy (computed nightly by polygon scanner)
    rvol     = relative volume ratio (premarket volume proxy)
    Returns (gap_pct, vol_ratio, direction, scan_date_str, status).
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT gap_pct, rvol, scan_date
                    FROM polygon_rvol_scan
                    WHERE ticker = %s
                    ORDER BY scan_date DESC
                    LIMIT 1
                """, (ticker.upper(),))
                row = cur.fetchone()
        if not row:
            return None, None, None, None, MISSING
        gap_pct, rvol_val, scan_date = row
        gap_pct  = float(gap_pct)  if gap_pct  is not None else None
        rvol_val = float(rvol_val) if rvol_val is not None else None
        scan_date_str = str(scan_date)
        # Staleness check
        days_old = (date.today() - scan_date).days
        status = STALE if days_old > _STALE_DAYS else AVAILABLE
        # Direction
        direction = "FLAT"
        if gap_pct is not None:
            if gap_pct >= _PREMARKET_GAP_BULL:
                direction = "GAP_UP"
            elif gap_pct <= _PREMARKET_GAP_BEAR:
                direction = "GAP_DOWN"
        return (
            round(gap_pct, 3) if gap_pct is not None else None,
            round(rvol_val, 2) if rvol_val is not None else None,
            direction, scan_date_str, status
        )
    except Exception as e:
        log.warning(f"[signal_engine] premarket {ticker}: {e}")
        return None, None, None, None, FAILED


def _compute_rvol(ticker: str) -> tuple:
    """
    Pull RVOL from polygon_rvol_scan (mandatory gate).
    Returns (rvol, status).  MISSING if no row found.
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT rvol, scan_date
                    FROM polygon_rvol_scan
                    WHERE ticker = %s
                    ORDER BY scan_date DESC
                    LIMIT 1
                """, (ticker.upper(),))
                row = cur.fetchone()
        if not row or row[0] is None:
            return None, MISSING
        rvol_val, scan_date = float(row[0]), row[1]
        days_old = (date.today() - scan_date).days
        status = STALE if days_old > _STALE_DAYS else AVAILABLE
        return round(rvol_val, 2), status
    except Exception as e:
        log.warning(f"[signal_engine] rvol {ticker}: {e}")
        return None, FAILED


def _compute_mtf(ticker: str) -> tuple:
    """
    Multi-timeframe alignment from aiem_multitimeframe.analyze_ticker.
    Returns (alignment_score, dominant_bias, bull_count, bear_count, conflict, timing, status).
    store=False: no DB writes.
    """
    try:
        from aiem_multitimeframe import analyze_ticker
        result = analyze_ticker(ticker, store=False)
        return (
            round(float(result.get("timeframe_alignment_score", 0)), 4),
            result.get("dominant_bias", NEUTRAL),
            int(result.get("bullish_tf_count", 0)),
            int(result.get("bearish_tf_count", 0)),
            round(float(result.get("conflict_score", 0)), 4),
            result.get("entry_timing_status", "INSUFFICIENT_DATA"),
            AVAILABLE,
        )
    except Exception as e:
        log.warning(f"[signal_engine] mtf {ticker}: {e}")
        return None, None, None, None, None, None, FAILED


def _compute_sector(ticker: str) -> tuple:
    """
    Sector relative strength vs SPY from sector_etf_daily.
    Returns (etf_ticker, sector_5d, spy_5d, relative, status).
    """
    try:
        from sector_etf_data import get_sector_etf_for_ticker
        etf = get_sector_etf_for_ticker(ticker.upper())
        if not etf:
            return None, None, None, None, NOT_APPLICABLE
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT etf_ticker,
                           SUM(return_pct) FILTER (WHERE price_date > CURRENT_DATE - 7) AS ret5d,
                           MAX(price_date) AS latest
                    FROM sector_etf_daily
                    WHERE etf_ticker IN (%s, 'SPY')
                      AND price_date >= CURRENT_DATE - 7
                    GROUP BY etf_ticker
                """, (etf,))
                rows = {r[0]: (float(r[1]) if r[1] is not None else None, r[2])
                        for r in cur.fetchall()}
        sector_row = rows.get(etf)
        spy_row    = rows.get("SPY")
        if not sector_row or not spy_row:
            return etf, None, None, None, MISSING
        sector_ret = sector_row[0]
        spy_ret    = spy_row[0]
        relative   = round(sector_ret - spy_ret, 3) if (sector_ret is not None and spy_ret is not None) else None
        latest = sector_row[1]
        days_old = (date.today() - latest).days if latest else 999
        status = STALE if days_old > _SECTOR_STALE_DAYS else AVAILABLE
        return (
            etf,
            round(sector_ret, 3) if sector_ret is not None else None,
            round(spy_ret, 3) if spy_ret is not None else None,
            relative,
            status,
        )
    except Exception as e:
        log.warning(f"[signal_engine] sector {ticker}: {e}")
        return None, None, None, None, FAILED


def _compute_regime() -> tuple:
    """
    Market regime from garch_regime_log (most recent row).
    Falls back to regime_history if garch_regime_log is empty.
    Returns (regime_label, source, status).
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'garch_regime_log'
                """)
                gcols = {r[0] for r in cur.fetchall()}
                if gcols:
                    cur.execute("""
                        SELECT * FROM garch_regime_log
                        ORDER BY logged_at DESC LIMIT 1
                    """)
                    row = cur.fetchone()
                    if row:
                        # Extract regime label from the row — column order varies
                        cols = [d[0] for d in cur.description]
                        d = dict(zip(cols, row))
                        # regime column may be called "regime_label" or "regime"
                        rl = d.get("regime_label") or d.get("regime") or "NEUTRAL"
                        return str(rl), "garch_regime_log", AVAILABLE
                # Fallback: regime_history
                cur.execute("""
                    SELECT regime_label FROM regime_history
                    ORDER BY recorded_at DESC LIMIT 1
                """)
                rh = cur.fetchone()
                if rh:
                    return str(rh[0]), "regime_history", AVAILABLE
        return None, None, MISSING
    except Exception as e:
        log.warning(f"[signal_engine] regime: {e}")
        return None, None, FAILED


def _compute_patterns(ticker: str, thesis: str = "NEUTRAL") -> tuple:
    """
    Pattern detection via repaired aiem_pattern_engine.detect_for_ticker.
    Returns (score, direction, confidence, name, timeframe, invalidation, families, status).
    Never returns score=0.5 from a broad exception.
    """
    try:
        from aiem_pattern_engine import detect_for_ticker
        result = detect_for_ticker(ticker, thesis=thesis)

        # Repaired detect_for_ticker returns None pattern_score on failure
        raw_status = result.get("status", "")
        if raw_status == "FAILED" or result.get("error"):
            return None, None, None, None, None, None, None, FAILED

        score = result.get("pattern_score")  # None = no PASS patterns fired (not 0.5)
        all_pats = result.get("all_patterns", [])

        direction   = None
        confidence  = None
        name        = None
        invalidation = None
        families_fired = []

        if all_pats:
            # Pick the highest-confidence pattern that has a directional call
            directional = [p for p in all_pats if p.get("direction") in (BULLISH, BEARISH)]
            if directional:
                best = max(directional, key=lambda p: float(p.get("confidence", 0)))
                direction   = best.get("direction")
                confidence  = float(best.get("confidence", 0))
                name        = best.get("pattern", "")
                invalidation = best.get("invalidation_level")
            # Track which families fired
            for fam in ("candlestick", "chart_structure", "harmonic", "wyckoff_vpa", "elliott_wave"):
                if result.get(fam):
                    families_fired.append(fam)

        families_str = ",".join(families_fired) if families_fired else None
        p_status = "NO_DATA" if not all_pats else AVAILABLE
        if score is None and not all_pats:
            p_status = "NO_DATA"
        elif result.get("error") == "insufficient bars":
            p_status = "INSUFFICIENT_BARS"

        return (
            round(score, 4) if score is not None else None,
            direction,
            round(confidence, 4) if confidence is not None else None,
            name,
            "daily",
            round(float(invalidation), 4) if invalidation is not None else None,
            families_str,
            p_status,
        )
    except Exception as e:
        log.warning(f"[signal_engine] patterns {ticker}: {e}")
        return None, None, None, None, None, None, None, FAILED


# ── Evidence aggregation and direction decision ────────────────────────────────

def _make_decision(
    rsi: Optional[float],
    rsi_signal: Optional[str],
    rsi_status: str,
    macd_hist: Optional[float],
    macd_cross: Optional[str],
    macd_status: str,
    adx: Optional[float],
    adx_di_plus: Optional[float],
    adx_di_minus: Optional[float],
    adx_trend: Optional[str],
    adx_status: str,
    price_vs_ema20: Optional[float],
    ema_status: str,
    vwap_pct_dev: Optional[float],
    vwap_status: str,
    bb_position: Optional[float],
    bb_status: str,
    pattern_score: Optional[float],
    pattern_status: str,
    mtf_bias: Optional[str],
    mtf_status: str,
    hurst: Optional[float],
    hurst_regime: Optional[str],
    hurst_status: str,
    premarket_gap: Optional[float],
    premarket_status: str,
    rvol_val: Optional[float],
    rvol_status: str,
    garch_vote: Optional[int],
) -> tuple:
    """
    Evidence aggregation → (thesis, signal_quality, confidence, blocking_reason).

    Each component casts one vote: BULLISH, BEARISH, or NEUTRAL (not counted).
    Minimum _MIN_CONFIRMING_SIGS same-direction votes required for a directional thesis.
    """
    bullish_ev: List[str] = []
    bearish_ev: List[str] = []
    neutral_ev: List[str] = []

    # ── RSI vote ──────────────────────────────────────────────────────────────
    if rsi_status in (AVAILABLE, STALE) and rsi is not None:
        if rsi_signal == "OVERSOLD":
            bullish_ev.append(f"rsi_oversold({rsi:.1f})")
        elif rsi_signal == "OVERBOUGHT":
            bearish_ev.append(f"rsi_overbought({rsi:.1f})")
        else:
            neutral_ev.append(f"rsi_neutral({rsi:.1f})")

    # ── MACD vote ─────────────────────────────────────────────────────────────
    if macd_status in (AVAILABLE, STALE) and macd_hist is not None:
        if macd_cross == "BULLISH_CROSS":
            bullish_ev.append("macd_bullish_cross")
        elif macd_cross == "BEARISH_CROSS":
            bearish_ev.append("macd_bearish_cross")
        elif macd_hist > 0:
            bullish_ev.append(f"macd_hist_positive({macd_hist:+.4f})")
        else:
            bearish_ev.append(f"macd_hist_negative({macd_hist:+.4f})")

    # ── ADX vote ──────────────────────────────────────────────────────────────
    if adx_status in (AVAILABLE, STALE) and adx is not None:
        if adx_trend == "TRENDING" and adx_di_plus is not None and adx_di_minus is not None:
            if adx_di_plus > adx_di_minus:
                bullish_ev.append(f"adx_trending_bullish(+DI={adx_di_plus:.1f},-DI={adx_di_minus:.1f})")
            else:
                bearish_ev.append(f"adx_trending_bearish(+DI={adx_di_plus:.1f},-DI={adx_di_minus:.1f})")
        else:
            neutral_ev.append(f"adx_ranging({adx:.1f})")

    # ── EMA vote ──────────────────────────────────────────────────────────────
    if ema_status in (AVAILABLE, STALE) and price_vs_ema20 is not None:
        if price_vs_ema20 > 0.20:
            bullish_ev.append(f"price_above_ema20(+{price_vs_ema20:.2f}%)")
        elif price_vs_ema20 < -0.20:
            bearish_ev.append(f"price_below_ema20({price_vs_ema20:.2f}%)")
        else:
            neutral_ev.append(f"price_near_ema20({price_vs_ema20:.2f}%)")

    # ── VWAP vote ─────────────────────────────────────────────────────────────
    if vwap_status in (AVAILABLE, STALE) and vwap_pct_dev is not None:
        if vwap_pct_dev > 0.10:
            bullish_ev.append(f"price_above_vwap(+{vwap_pct_dev:.2f}%)")
        elif vwap_pct_dev < -0.10:
            bearish_ev.append(f"price_below_vwap({vwap_pct_dev:.2f}%)")
        else:
            neutral_ev.append("price_at_vwap")

    # ── Bollinger vote ────────────────────────────────────────────────────────
    if bb_status in (AVAILABLE, STALE) and bb_position is not None:
        if bb_position < _BB_LOWER_ZONE:
            bullish_ev.append(f"bb_lower_zone(pos={bb_position:.2f})")
        elif bb_position > _BB_UPPER_ZONE:
            bearish_ev.append(f"bb_upper_zone(pos={bb_position:.2f})")
        else:
            neutral_ev.append(f"bb_mid(pos={bb_position:.2f})")

    # ── Pattern vote ──────────────────────────────────────────────────────────
    if pattern_status in (AVAILABLE,) and pattern_score is not None:
        if pattern_score > 0.65:
            bullish_ev.append(f"pattern_confirming(score={pattern_score:.2f})")
        elif pattern_score < 0.35:
            bearish_ev.append(f"pattern_contra(score={pattern_score:.2f})")
        else:
            neutral_ev.append(f"pattern_neutral(score={pattern_score:.2f})")

    # ── MTF vote ──────────────────────────────────────────────────────────────
    if mtf_status in (AVAILABLE,) and mtf_bias is not None:
        if mtf_bias == BULLISH:
            bullish_ev.append(f"mtf_bullish_alignment")
        elif mtf_bias == BEARISH:
            bearish_ev.append(f"mtf_bearish_alignment")
        else:
            neutral_ev.append("mtf_neutral")

    # ── Premarket vote ────────────────────────────────────────────────────────
    if premarket_status in (AVAILABLE, STALE) and premarket_gap is not None:
        if premarket_gap >= _PREMARKET_GAP_BULL:
            bullish_ev.append(f"premarket_gap_up({premarket_gap:+.1f}%)")
        elif premarket_gap <= _PREMARKET_GAP_BEAR:
            bearish_ev.append(f"premarket_gap_down({premarket_gap:+.1f}%)")
        else:
            neutral_ev.append(f"premarket_flat({premarket_gap:+.1f}%)")

    # ── Hurst modifier (amplifies or dampens trend signals) ───────────────────
    if hurst_status in (AVAILABLE,) and hurst is not None:
        if hurst_regime == "TRENDING" and len(bullish_ev) > len(bearish_ev):
            bullish_ev.append(f"hurst_trending_confirms_bull(H={hurst:.2f})")
        elif hurst_regime == "TRENDING" and len(bearish_ev) > len(bullish_ev):
            bearish_ev.append(f"hurst_trending_confirms_bear(H={hurst:.2f})")
        elif hurst_regime == "MEAN_REVERTING":
            neutral_ev.append(f"hurst_mean_reverting(H={hurst:.2f})")

    # ── GARCH risk-off dampener ───────────────────────────────────────────────
    if garch_vote is not None and garch_vote == -1:
        # Adds a bearish risk-off vote (dampens bullish confidence)
        bearish_ev.append("garch_risk_off_vol_rising")
    elif garch_vote is not None and garch_vote == 1:
        bullish_ev.append("garch_risk_on_vol_falling")

    # ── Decision logic ────────────────────────────────────────────────────────
    bull_count = len(bullish_ev)
    bear_count = len(bearish_ev)
    total = bull_count + bear_count
    if total == 0:
        return (NO_TRADE, INSUFFICIENT, 0.0, "no_valid_votes",
                tuple(bullish_ev), tuple(bearish_ev), tuple(neutral_ev))

    dominant = bull_count if bull_count >= bear_count else bear_count
    min_count = min(bull_count, bear_count)
    confidence_raw = (dominant - min_count) / total

    # Signal quality based on confirming votes
    if dominant >= 6:
        quality = STRONG
    elif dominant >= 4:
        quality = MODERATE
    elif dominant >= _MIN_CONFIRMING_SIGS:
        quality = WEAK
    else:
        quality = INSUFFICIENT

    if quality == INSUFFICIENT:
        return (NO_TRADE, INSUFFICIENT, round(confidence_raw, 3),
                f"insufficient_signals(bull={bull_count},bear={bear_count})",
                tuple(bullish_ev), tuple(bearish_ev), tuple(neutral_ev))

    if confidence_raw < _MIN_CONFIDENCE:
        return (NEUTRAL, quality, round(confidence_raw, 3), None,
                tuple(bullish_ev), tuple(bearish_ev), tuple(neutral_ev))

    thesis = BULLISH if bull_count >= bear_count else BEARISH
    return (thesis, quality, round(confidence_raw, 3), None,
            tuple(bullish_ev), tuple(bearish_ev), tuple(neutral_ev))


# ── Main entry point ──────────────────────────────────────────────────────────

def run_signal_engine(ticker: str, hint_thesis: str = "UNDECIDED") -> SignalResult:
    """
    Compute a full SignalResult for ticker.

    hint_thesis: passed to pattern engine as starting thesis (Phase 1 seeds UNDECIDED).
    All components computed independently.  Module failures are caught and logged;
    the SignalResult records them in failed_modules.  The RVOL gate is mandatory:
    if rvol_status = MISSING, thesis = NO_TRADE.
    """
    now = datetime.utcnow().isoformat() + "Z"
    failed: List[str] = []

    # ── 1. Fetch OHLCV bars (all technicals depend on this) ─────────────────
    bars = _fetch_bars(ticker, lookback=120)
    closes = [b["close"] for b in bars]
    if len(bars) < _MIN_BARS_TECHNICAL:
        return SignalResult(
            ticker=ticker, computed_at=now,
            thesis=NO_TRADE, signal_quality=INSUFFICIENT,
            confidence=0.0, blocking_reason=f"insufficient_bars({len(bars)})",
            vwap_value=None, vwap_pct_deviation=None, vwap_reclaim=None, vwap_status=MISSING,
            ema_9=None, ema_20=None, ema_50=None, sma_50=None, sma_200=None,
            price_vs_ema20_pct=None, ema_status=MISSING,
            adx=None, adx_di_plus=None, adx_di_minus=None, adx_trend=None, adx_status=MISSING,
            rsi=None, rsi_signal=None, rsi_status=MISSING,
            macd_line=None, macd_signal_line=None, macd_hist=None, macd_cross=None, macd_status=MISSING,
            atr=None, atr_pct=None, atr_status=MISSING,
            bb_upper=None, bb_mid=None, bb_lower=None, bb_position=None, bb_squeeze=None, bb_status=MISSING,
            rvol=None, rvol_status=MISSING,
            avg_volume_20d=None, volume_trend=None, vol_climax=None, volume_status=MISSING,
            nearest_support=None, nearest_resistance=None,
            price_vs_support_pct=None, price_vs_resist_pct=None, sr_status=MISSING,
            garch_forecast_vol=None, garch_persistence=None, garch_regime_vote=None,
            garch_reason=None, garch_status=MISSING,
            vpin_score=None, vpin_signal=None, vpin_status=MISSING,
            hurst_exponent_val=None, hurst_regime=None, hurst_status=MISSING,
            pattern_score=None, pattern_direction=None, pattern_confidence=None,
            pattern_name=None, pattern_timeframe=None, pattern_invalidation=None,
            pattern_families_fired=None, pattern_status=MISSING,
            premarket_gap_pct=None, premarket_volume_ratio=None, premarket_direction=None,
            premarket_scan_date=None, premarket_status=MISSING,
            mtf_alignment_score=None, mtf_dominant_bias=None, mtf_bull_tf_count=None,
            mtf_bear_tf_count=None, mtf_conflict_score=None, mtf_entry_timing=None, mtf_status=MISSING,
            sector_etf=None, sector_5d_return_pct=None, spy_5d_return_pct=None,
            sector_relative_strength_pct=None, breadth_status=MISSING,
            regime=None, regime_source=None, regime_status=MISSING,
            bullish_evidence=(), bearish_evidence=(), neutral_evidence=(),
            failed_modules=(f"bars:only_{len(bars)}_available",),
        )

    # ── 2. Mandatory: RVOL gate ──────────────────────────────────────────────
    rvol_val, rvol_status = _compute_rvol(ticker)
    if rvol_status == FAILED:
        failed.append("rvol:fetch_failed")
    if rvol_status == MISSING:
        # Hard block — no execution context without universe scan data
        return SignalResult(
            ticker=ticker, computed_at=now,
            thesis=NO_TRADE, signal_quality=INSUFFICIENT,
            confidence=0.0, blocking_reason="rvol_missing:no_universe_scan_data",
            vwap_value=None, vwap_pct_deviation=None, vwap_reclaim=None, vwap_status=MISSING,
            ema_9=None, ema_20=None, ema_50=None, sma_50=None, sma_200=None,
            price_vs_ema20_pct=None, ema_status=MISSING,
            adx=None, adx_di_plus=None, adx_di_minus=None, adx_trend=None, adx_status=MISSING,
            rsi=None, rsi_signal=None, rsi_status=MISSING,
            macd_line=None, macd_signal_line=None, macd_hist=None, macd_cross=None, macd_status=MISSING,
            atr=None, atr_pct=None, atr_status=MISSING,
            bb_upper=None, bb_mid=None, bb_lower=None, bb_position=None, bb_squeeze=None, bb_status=MISSING,
            rvol=None, rvol_status=MISSING,
            avg_volume_20d=None, volume_trend=None, vol_climax=None, volume_status=MISSING,
            nearest_support=None, nearest_resistance=None,
            price_vs_support_pct=None, price_vs_resist_pct=None, sr_status=MISSING,
            garch_forecast_vol=None, garch_persistence=None, garch_regime_vote=None,
            garch_reason=None, garch_status=MISSING,
            vpin_score=None, vpin_signal=None, vpin_status=MISSING,
            hurst_exponent_val=None, hurst_regime=None, hurst_status=MISSING,
            pattern_score=None, pattern_direction=None, pattern_confidence=None,
            pattern_name=None, pattern_timeframe=None, pattern_invalidation=None,
            pattern_families_fired=None, pattern_status=MISSING,
            premarket_gap_pct=None, premarket_volume_ratio=None, premarket_direction=None,
            premarket_scan_date=None, premarket_status=MISSING,
            mtf_alignment_score=None, mtf_dominant_bias=None, mtf_bull_tf_count=None,
            mtf_bear_tf_count=None, mtf_conflict_score=None, mtf_entry_timing=None, mtf_status=MISSING,
            sector_etf=None, sector_5d_return_pct=None, spy_5d_return_pct=None,
            sector_relative_strength_pct=None, breadth_status=MISSING,
            regime=None, regime_source=None, regime_status=MISSING,
            bullish_evidence=(), bearish_evidence=(), neutral_evidence=(),
            failed_modules=("rvol:missing — INSUFFICIENT_DATA gate triggered",),
        )

    # ── 3. All optional components ────────────────────────────────────────────
    vwap_val, vwap_dev, vwap_reclaim, vwap_status = _compute_vwap(bars)
    if vwap_status == FAILED:
        failed.append("vwap:compute_failed")

    ema9, ema20, ema50, sma50, sma200, vs_ema20, ema_status = _compute_ema_sma(closes)
    if ema_status == FAILED:
        failed.append("ema_sma:compute_failed")

    adx, dip, dim, adx_trend, adx_status = _compute_adx(bars)
    if adx_status == FAILED:
        failed.append("adx:compute_failed")

    rsi_val, rsi_sig, rsi_status = _compute_rsi(closes)
    if rsi_status == FAILED:
        failed.append("rsi:compute_failed")

    macd_line, macd_sl, macd_hist, macd_cross, macd_status = _compute_macd(closes)
    if macd_status == FAILED:
        failed.append("macd:compute_failed")

    atr_val, atr_pct, atr_status = _compute_atr(bars)
    if atr_status == FAILED:
        failed.append("atr:compute_failed")

    bb_up, bb_mid, bb_lo, bb_pos, bb_sq, bb_status = _compute_bollinger(closes)
    if bb_status == FAILED:
        failed.append("bollinger:compute_failed")

    avg_vol, vol_trend, vol_climax, vol_status = _compute_volume_profile(bars)
    if vol_status == FAILED:
        failed.append("volume_profile:compute_failed")

    sup, res, vs_sup, vs_res, sr_status = _compute_sr(bars)
    if sr_status == FAILED:
        failed.append("support_resistance:compute_failed")

    garch_fv, garch_pers, garch_vote, garch_reason, garch_status = _compute_garch(closes)
    if garch_status == FAILED:
        failed.append("garch:compute_failed")

    vpin_s, vpin_sig, vpin_status = _compute_vpin(bars)
    if vpin_status == FAILED:
        failed.append("vpin:compute_failed")

    hurst_v, hurst_reg, hurst_status = _compute_hurst(closes)
    if hurst_status == FAILED:
        failed.append("hurst:compute_failed")

    pm_gap, pm_vol, pm_dir, pm_date, pm_status = _compute_premarket(ticker)
    if pm_status == FAILED:
        failed.append("premarket:fetch_failed")

    mtf_align, mtf_bias, mtf_bull, mtf_bear, mtf_conf, mtf_timing, mtf_status = _compute_mtf(ticker)
    if mtf_status == FAILED:
        failed.append("mtf:compute_failed")

    sec_etf, sec5d, spy5d, sec_rel, sec_status = _compute_sector(ticker)
    if sec_status == FAILED:
        failed.append("sector:compute_failed")

    regime_label, regime_src, regime_status = _compute_regime()
    if regime_status == FAILED:
        failed.append("regime:fetch_failed")

    # Pattern engine uses hint thesis; if UNDECIDED, pass NEUTRAL
    pat_thesis = NEUTRAL if hint_thesis == "UNDECIDED" else hint_thesis
    (pat_score, pat_dir, pat_conf, pat_name,
     pat_tf, pat_inv, pat_fam, pat_status) = _compute_patterns(ticker, pat_thesis)
    if pat_status == FAILED:
        failed.append("patterns:compute_failed")

    # ── 4. Direction decision ─────────────────────────────────────────────────
    (thesis, quality, confidence, blocking,
     bull_ev, bear_ev, neut_ev) = _make_decision(
        rsi=rsi_val, rsi_signal=rsi_sig, rsi_status=rsi_status,
        macd_hist=macd_hist, macd_cross=macd_cross, macd_status=macd_status,
        adx=adx, adx_di_plus=dip, adx_di_minus=dim,
        adx_trend=adx_trend, adx_status=adx_status,
        price_vs_ema20=vs_ema20, ema_status=ema_status,
        vwap_pct_dev=vwap_dev, vwap_status=vwap_status,
        bb_position=bb_pos, bb_status=bb_status,
        pattern_score=pat_score, pattern_status=pat_status,
        mtf_bias=mtf_bias, mtf_status=mtf_status,
        hurst=hurst_v, hurst_regime=hurst_reg, hurst_status=hurst_status,
        premarket_gap=pm_gap, premarket_status=pm_status,
        rvol_val=rvol_val, rvol_status=rvol_status,
        garch_vote=garch_vote,
    )

    return SignalResult(
        ticker=ticker, computed_at=now,
        thesis=thesis, signal_quality=quality,
        confidence=confidence, blocking_reason=blocking,
        # VWAP
        vwap_value=vwap_val, vwap_pct_deviation=vwap_dev,
        vwap_reclaim=vwap_reclaim, vwap_status=vwap_status,
        # EMA/SMA
        ema_9=ema9, ema_20=ema20, ema_50=ema50,
        sma_50=sma50, sma_200=sma200,
        price_vs_ema20_pct=vs_ema20, ema_status=ema_status,
        # ADX
        adx=adx, adx_di_plus=dip, adx_di_minus=dim,
        adx_trend=adx_trend, adx_status=adx_status,
        # RSI
        rsi=rsi_val, rsi_signal=rsi_sig, rsi_status=rsi_status,
        # MACD
        macd_line=macd_line, macd_signal_line=macd_sl,
        macd_hist=macd_hist, macd_cross=macd_cross, macd_status=macd_status,
        # ATR
        atr=atr_val, atr_pct=atr_pct, atr_status=atr_status,
        # Bollinger
        bb_upper=bb_up, bb_mid=bb_mid, bb_lower=bb_lo,
        bb_position=bb_pos, bb_squeeze=bb_sq, bb_status=bb_status,
        # RVOL
        rvol=rvol_val, rvol_status=rvol_status,
        # Volume
        avg_volume_20d=avg_vol, volume_trend=vol_trend,
        vol_climax=vol_climax, volume_status=vol_status,
        # S/R
        nearest_support=sup, nearest_resistance=res,
        price_vs_support_pct=vs_sup, price_vs_resist_pct=vs_res, sr_status=sr_status,
        # GARCH
        garch_forecast_vol=garch_fv, garch_persistence=garch_pers,
        garch_regime_vote=garch_vote, garch_reason=garch_reason, garch_status=garch_status,
        # VPIN
        vpin_score=vpin_s, vpin_signal=vpin_sig, vpin_status=vpin_status,
        # Hurst
        hurst_exponent_val=hurst_v, hurst_regime=hurst_reg, hurst_status=hurst_status,
        # Patterns
        pattern_score=pat_score, pattern_direction=pat_dir,
        pattern_confidence=pat_conf, pattern_name=pat_name,
        pattern_timeframe=pat_tf, pattern_invalidation=pat_inv,
        pattern_families_fired=pat_fam, pattern_status=pat_status,
        # Premarket
        premarket_gap_pct=pm_gap, premarket_volume_ratio=pm_vol,
        premarket_direction=pm_dir, premarket_scan_date=pm_date,
        premarket_status=pm_status,
        # MTF
        mtf_alignment_score=mtf_align, mtf_dominant_bias=mtf_bias,
        mtf_bull_tf_count=mtf_bull, mtf_bear_tf_count=mtf_bear,
        mtf_conflict_score=mtf_conf, mtf_entry_timing=mtf_timing,
        mtf_status=mtf_status,
        # Sector
        sector_etf=sec_etf, sector_5d_return_pct=sec5d,
        spy_5d_return_pct=spy5d,
        sector_relative_strength_pct=sec_rel, breadth_status=sec_status,
        # Regime
        regime=regime_label, regime_source=regime_src, regime_status=regime_status,
        # Evidence
        bullish_evidence=bull_ev, bearish_evidence=bear_ev,
        neutral_evidence=neut_ev, failed_modules=tuple(failed),
    )


def signal_result_to_dict(sr: SignalResult) -> Dict[str, Any]:
    """Convert SignalResult to a plain dict (JSON-serialisable)."""
    import dataclasses
    d = dataclasses.asdict(sr)
    # Convert tuples back to lists for JSON
    for k in ("bullish_evidence", "bearish_evidence", "neutral_evidence", "failed_modules"):
        d[k] = list(d[k])
    return d
