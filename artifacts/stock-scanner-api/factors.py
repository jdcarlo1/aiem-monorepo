import numpy as np
import ta


def momentum_factor(df) -> float:
    try:
        close = df["Close"].squeeze().astype(float)
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
        if np.isnan(rsi):
            return 0.5
        return round(float(max(0.0, min(1.0, (rsi - 30) / 40))), 4)
    except Exception:
        return 0.5


def volatility_factor(df) -> float:
    try:
        close = df["Close"].squeeze().astype(float)
        vol = close.pct_change().rolling(20).std().iloc[-1]
        if np.isnan(vol):
            return 0.5
        return round(float(max(0.0, min(1.0, vol / 0.05))), 4)
    except Exception:
        return 0.5


def volume_factor(df) -> float:
    try:
        volume = df["Volume"].squeeze().astype(float)
        avg_vol = volume.rolling(20).mean()
        vol_ratio = (volume / avg_vol).iloc[-1]
        if np.isnan(vol_ratio):
            return 0.5
        return round(float(max(0.0, min(1.0, (vol_ratio - 0.5) / 1.5))), 4)
    except Exception:
        return 0.5


def trend_factor(df) -> float:
    try:
        close = df["Close"].squeeze().astype(float)
        sma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator().iloc[-1]
        sma200 = ta.trend.SMAIndicator(close, window=200).sma_indicator().iloc[-1]
        price = close.iloc[-1]
        if np.isnan(sma50) or np.isnan(sma200):
            return 0.5
        score = 0.0
        if price > sma50:
            score += 0.4
        if price > sma200:
            score += 0.3
        if sma50 > sma200:
            score += 0.3
        return round(float(score), 4)
    except Exception:
        return 0.5
