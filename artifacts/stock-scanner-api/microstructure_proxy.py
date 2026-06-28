"""
microstructure_proxy.py
---------------------------
Approximates three microstructure signals using OHLCV data only,
because this codebase lacks Level-2 order book data.

WHAT IS BEING APPROXIMATED AND HOW
------------------------------------

1. BID-ASK SPREAD PROXY  (Corwin-Schultz estimator, 2012)
   ----------------------------------------------------
   True bid-ask spread can't be read without L2 data. The Corwin-Schultz
   estimator derives a spread proxy entirely from daily high/low prices.
   The intuition: a stock's observed high is close to the ask price and
   its observed low is close to the bid price. The spread can therefore
   be estimated from the high/low ratio over rolling windows.

   Substitute data used: daily high, low, close from polygon_market_daily
   (same table used everywhere else in the stack).

   Output: estimated half-spread as a fraction of price (e.g. 0.003 = 0.3%).
   Interpretation: higher = wider spread = less liquid = higher transaction
   cost = harder to enter/exit without slippage.

2. ORDER FLOW IMBALANCE PROXY  (volume-signed by return direction)
   ---------------------------------------------------------------
   True order flow imbalance is buy volume minus sell volume. Without
   a tick database, we estimate the sign of each bar using the Lee-Ready
   algorithm proxy: if close > open, the bar is "buyer-initiated" (volume
   is positive); if close < open, the bar is "seller-initiated" (volume
   is negative).

   Output: OFI ratio = net_signed_volume / total_volume over N days.
   Range: -1 (all sellers) to +1 (all buyers).
   Interpretation: sustained positive OFI is a demand-side accumulation
   signal; negative OFI with flat price suggests distribution/supply wall.

3. KYLE'S LAMBDA (price impact coefficient)
   -----------------------------------------
   In Kyle (1985), lambda measures how much the price moves per unit of
   order flow. We estimate it as:
     λ = Cov(|return|, volume) / Var(volume)
   using rolling windows. Higher lambda = price moves a lot per share
   traded = illiquid stock, large hidden order impact. Low lambda = thick
   book, hard to move the price.

   Substitute data used: daily volume and absolute return from
   polygon_market_daily.

CALLERS: None in main.py yet. Designed to be called from conviction
scoring or Layer 9 as a liquidity-adjusted signal component.

LIMITATIONS (honest): all three estimates degrade for high-cap mega-
stocks that trade continuously tight. They are most informative for
mid/small/micro-cap names where spread, OFI, and price impact vary
meaningfully from day to day.
"""

import os
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from typing import Optional, Dict, Any


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No DATABASE_URL set.")
    return psycopg2.connect(url)


def _fetch_ohlcv(ticker: str, lookback: int = 60) -> Optional[pd.DataFrame]:
    """Pull from polygon_market_daily, fallback to yfinance."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT date::date AS date, open_price AS open,
                           high_price AS high, low_price AS low,
                           close_price AS close, volume
                    FROM polygon_market_daily
                    WHERE ticker = %s
                    ORDER BY date DESC LIMIT %s
                """, (ticker, lookback))
                rows = cur.fetchall()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").reset_index(drop=True)
    except Exception:
        pass

    try:
        import yfinance as yf
        raw = yf.download(ticker, period="3mo", progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.reset_index()
        raw.columns = [c.lower() for c in raw.columns]
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in raw.columns:
                raw[col] = np.nan
        return raw[["date", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    except Exception:
        return None


def corwin_schultz_spread(df: pd.DataFrame, window: int = 5) -> Optional[float]:
    """
    Corwin-Schultz (2012) bid-ask spread estimator from daily H/L.
    Returns estimated half-spread as fraction of price (e.g. 0.003).
    Returns None if insufficient data.
    """
    if df is None or len(df) < window + 2:
        return None

    w = df.tail(window + 1).copy()
    hi = w["high"].values.astype(float)
    lo = w["low"].values.astype(float)

    # Corwin-Schultz: beta = [ln(H_t/L_t)]^2 + [ln(H_t+1/L_t+1)]^2 over 2-day windows
    # gamma = [ln(max(H_t, H_t+1) / min(L_t, L_t+1))]^2
    betas, gammas = [], []
    for i in range(len(w) - 1):
        if lo[i] > 0 and lo[i + 1] > 0:
            b = (np.log(hi[i] / lo[i])) ** 2 + (np.log(hi[i + 1] / lo[i + 1])) ** 2
            g = (np.log(max(hi[i], hi[i + 1]) / min(lo[i], lo[i + 1]))) ** 2
            betas.append(b)
            gammas.append(g)

    if not betas:
        return None

    beta = float(np.mean(betas))
    gamma = float(np.mean(gammas))

    # Derived formula: alpha = (sqrt(2*beta) - sqrt(beta)) / (3 - 2*sqrt(2)) - sqrt(gamma / (3 - 2*sqrt(2)))
    k1 = 3.0 - 2.0 * np.sqrt(2.0)
    try:
        alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / k1 - np.sqrt(gamma / k1)
        spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
        spread = max(spread, 0.0)  # clamp negatives (noise artifact)
        return round(float(spread), 6)
    except Exception:
        return None


def order_flow_imbalance(df: pd.DataFrame, window: int = 5) -> Optional[float]:
    """
    Lee-Ready OFI proxy: sign each bar by close vs open, weight by volume.
    Returns OFI ratio in [-1, +1]: +1 = all buyer-initiated, -1 = all seller.
    """
    if df is None or len(df) < window:
        return None

    w = df.tail(window).copy()
    w["signed_vol"] = np.where(
        w["close"] > w["open"], w["volume"],
        np.where(w["close"] < w["open"], -w["volume"], 0.0)
    )
    total_vol = w["volume"].sum()
    if total_vol == 0:
        return None
    ofi = float(w["signed_vol"].sum() / total_vol)
    return round(ofi, 4)


def kyle_lambda(df: pd.DataFrame, window: int = 20) -> Optional[float]:
    """
    Kyle's lambda (price impact): Cov(|return|, volume) / Var(volume).
    Higher = more illiquid, larger price impact per share traded.
    Units: price-move fraction per unit of volume (normalized).
    """
    if df is None or len(df) < window:
        return None

    w = df.tail(window).copy()
    w["abs_ret"] = (w["close"] / w["close"].shift(1) - 1.0).abs()
    w = w.dropna(subset=["abs_ret"])

    if len(w) < 5:
        return None

    vol = w["volume"].values.astype(float)
    ret = w["abs_ret"].values.astype(float)

    vol_var = float(np.var(vol))
    if vol_var == 0:
        return None

    cov = float(np.cov(ret, vol)[0, 1])
    lam = cov / vol_var

    # Normalize by mean price so it's comparable across price levels
    mean_price = float(w["close"].mean())
    if mean_price > 0:
        lam = lam / mean_price

    return round(float(lam), 10)


def compute_microstructure_proxy(ticker: str, window: int = 5) -> Dict[str, Any]:
    """
    Master function: returns all three microstructure proxies for a ticker.

    Returns dict with:
      spread_est       — Corwin-Schultz estimated half-spread (fraction of price)
      ofi              — Order flow imbalance ratio (-1 to +1)
      kyle_lambda      — Price impact coefficient (normalized)
      spread_bps       — spread_est in basis points (for human readability)
      ofi_signal       — 'buying_pressure'/'selling_pressure'/'neutral'
      liquidity_score  — 0-100 composite (100 = most liquid), for ranking
      data_source      — 'polygon_market_daily' or 'yfinance_fallback'
    """
    df = _fetch_ohlcv(ticker, lookback=max(window + 30, 60))
    if df is None or df.empty:
        return {"ticker": ticker, "error": "No OHLCV data available"}

    data_source = "polygon_market_daily" if len(df) >= 20 else "yfinance_fallback"

    spread = corwin_schultz_spread(df, window=window)
    ofi = order_flow_imbalance(df, window=window)
    lam = kyle_lambda(df, window=20)

    # OFI signal label
    if ofi is None:
        ofi_signal = "unknown"
    elif ofi > 0.20:
        ofi_signal = "buying_pressure"
    elif ofi < -0.20:
        ofi_signal = "selling_pressure"
    else:
        ofi_signal = "neutral"

    # Composite liquidity score: penalize wide spread + high lambda
    # (normalized heuristically — not calibrated, relative ranking only)
    score = 50.0
    if spread is not None:
        score -= min(spread * 10000, 40.0)  # each bp of spread costs up to 40 pts
    if lam is not None and lam > 0:
        score -= min(np.log1p(lam * 1e8) * 2, 20.0)
    if ofi is not None:
        score += ofi * 10.0  # buying pressure adds a small bonus
    liquidity_score = max(0.0, min(100.0, score))

    return {
        "ticker":          ticker,
        "spread_est":      spread,
        "spread_bps":      round(spread * 10000, 1) if spread is not None else None,
        "ofi":             ofi,
        "ofi_signal":      ofi_signal,
        "kyle_lambda":     lam,
        "liquidity_score": round(liquidity_score, 1),
        "window_days":     window,
        "data_source":     data_source,
    }
