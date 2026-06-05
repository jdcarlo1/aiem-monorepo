import pandas as pd
import ta
import numpy as np


def compute_indicators(df: pd.DataFrame) -> dict:
    if df is None or df.empty or len(df) < 20:
        return {}

    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    volume = df["Volume"].squeeze()

    result = {}

    try:
        macd_ind = ta.trend.MACD(close)
        result["macd"] = _safe_float(macd_ind.macd().iloc[-1])
        result["macd_signal"] = _safe_float(macd_ind.macd_signal().iloc[-1])
        result["macd_hist"] = _safe_float(macd_ind.macd_diff().iloc[-1])
    except Exception:
        pass

    try:
        rsi_ind = ta.momentum.RSIIndicator(close, window=14)
        result["rsi"] = _safe_float(rsi_ind.rsi().iloc[-1])
    except Exception:
        pass

    try:
        bb_ind = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        result["bb_upper"] = _safe_float(bb_ind.bollinger_hband().iloc[-1])
        result["bb_mid"] = _safe_float(bb_ind.bollinger_mavg().iloc[-1])
        result["bb_lower"] = _safe_float(bb_ind.bollinger_lband().iloc[-1])
        result["bb_width"] = _safe_float(bb_ind.bollinger_wband().iloc[-1])
    except Exception:
        pass

    try:
        sma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        result["sma50"] = _safe_float(sma50.iloc[-1])
    except Exception:
        pass

    try:
        sma200 = ta.trend.SMAIndicator(close, window=200).sma_indicator()
        result["sma200"] = _safe_float(sma200.iloc[-1])
    except Exception:
        pass

    try:
        result["price"] = _safe_float(close.iloc[-1])
        result["price_change"] = _safe_float(close.iloc[-1] - close.iloc[-2])
        result["price_change_pct"] = _safe_float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100)
        result["volume"] = int(volume.iloc[-1])
        result["avg_volume_20"] = int(volume.tail(20).mean())
        result["volume_ratio"] = _safe_float(volume.iloc[-1] / volume.tail(20).mean())
    except Exception:
        pass

    try:
        atr_ind = ta.volatility.AverageTrueRange(high, low, close, window=14)
        result["atr"] = _safe_float(atr_ind.average_true_range().iloc[-1])
    except Exception:
        pass

    try:
        mom = ta.momentum.ROCIndicator(close, window=10)
        result["momentum"] = _safe_float(mom.roc().iloc[-1])
    except Exception:
        pass

    try:
        high_52w = float(high.tail(252).max())
        low_52w = float(low.tail(252).min())
        result["high_52w"] = high_52w
        result["low_52w"] = low_52w
        result["pct_from_52w_high"] = _safe_float((close.iloc[-1] - high_52w) / high_52w * 100)
    except Exception:
        pass

    result["history"] = build_history(df)

    return result


def _safe_float(val):
    try:
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return None
        return round(v, 4)
    except Exception:
        return None


def build_history(df: pd.DataFrame, periods: int = 90) -> list:
    try:
        subset = df.tail(periods).copy()
        rows = []
        for idx, row in subset.iterrows():
            rows.append({
                "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })
        return rows
    except Exception:
        return []
