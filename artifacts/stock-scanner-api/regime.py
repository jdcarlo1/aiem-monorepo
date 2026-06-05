import numpy as np


def detect_regime(df) -> str:
    close = df["Close"].squeeze().astype(float)

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()

    sma50_last = sma50.iloc[-1]
    if np.isnan(sma50_last) or sma50_last == 0:
        return "CHOPPY"

    trend_strength = abs(sma20.iloc[-1] - sma50_last) / sma50_last
    volatility = close.pct_change().rolling(20).std().iloc[-1]

    if np.isnan(trend_strength) or np.isnan(volatility):
        return "CHOPPY"

    if trend_strength > 0.03 and volatility < 0.02:
        return "TRENDING"

    if volatility > 0.03:
        return "HIGH_VOL"

    return "CHOPPY"
