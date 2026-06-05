import numpy as np
import pandas as pd
import ta
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import warnings
warnings.filterwarnings("ignore")


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    volume = df["Volume"].squeeze()

    feats = pd.DataFrame(index=df.index)

    try:
        feats["rsi"] = ta.momentum.RSIIndicator(close, window=14).rsi()
    except Exception:
        feats["rsi"] = 50.0

    try:
        macd_ind = ta.trend.MACD(close)
        feats["macd"] = macd_ind.macd()
        feats["macd_hist"] = macd_ind.macd_diff()
    except Exception:
        feats["macd"] = 0.0
        feats["macd_hist"] = 0.0

    try:
        bb_ind = ta.volatility.BollingerBands(close, window=20)
        bb_upper = bb_ind.bollinger_hband()
        bb_lower = bb_ind.bollinger_lband()
        feats["bb_pct"] = (close - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)
    except Exception:
        feats["bb_pct"] = 0.5

    try:
        sma50 = ta.trend.SMAIndicator(close, window=50).sma_indicator()
        sma200 = ta.trend.SMAIndicator(close, window=200).sma_indicator()
        feats["price_vs_sma50"] = (close - sma50) / sma50
        feats["price_vs_sma200"] = (close - sma200) / sma200
    except Exception:
        feats["price_vs_sma50"] = 0.0
        feats["price_vs_sma200"] = 0.0

    try:
        vol_avg = volume.rolling(20).mean()
        feats["volume_ratio"] = volume / vol_avg
    except Exception:
        feats["volume_ratio"] = 1.0

    try:
        feats["return_1d"] = close.pct_change(1)
        feats["return_5d"] = close.pct_change(5)
        feats["return_10d"] = close.pct_change(10)
    except Exception:
        feats["return_1d"] = 0.0
        feats["return_5d"] = 0.0
        feats["return_10d"] = 0.0

    return feats


def predict_direction(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 60:
        return {"probability_up": 50.0, "probability_down": 50.0, "direction": "Neutral", "confidence": "Low", "note": "Insufficient data"}

    try:
        feats = build_features(df)
        close = df["Close"].squeeze()
        labels = (close.shift(-1) > close).astype(int)

        combined = feats.copy()
        combined["label"] = labels
        combined = combined.dropna()

        if len(combined) < 30:
            return {"probability_up": 50.0, "probability_down": 50.0, "direction": "Neutral", "confidence": "Low", "note": "Insufficient clean data"}

        feature_cols = [c for c in combined.columns if c != "label"]
        X = combined[feature_cols].values
        y = combined["label"].values

        split = int(len(X) * 0.85)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s = scaler.transform(X_test)

        model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
        model.fit(X_train_s, y_train)

        last_feat = feats.iloc[-1:].copy()
        last_feat = last_feat.fillna(0)
        last_scaled = scaler.transform(last_feat[feature_cols].values)
        prob = model.predict_proba(last_scaled)[0]

        prob_up = float(prob[1]) if len(prob) > 1 else 0.5
        test_acc = float(model.score(X_test_s, y_test)) if len(X_test) > 0 else None

        if prob_up >= 0.65:
            direction = "Up"
            confidence = "High" if prob_up >= 0.75 else "Medium"
        elif prob_up <= 0.35:
            direction = "Down"
            confidence = "High" if prob_up <= 0.25 else "Medium"
        else:
            direction = "Neutral"
            confidence = "Low"

        return {
            "probability_up": round(prob_up * 100, 1),
            "probability_down": round((1 - prob_up) * 100, 1),
            "direction": direction,
            "confidence": confidence,
            "model_accuracy": round(test_acc * 100, 1) if test_acc is not None else None,
        }
    except Exception as e:
        return {"probability_up": 50.0, "probability_down": 50.0, "direction": "Neutral", "confidence": "Low", "note": str(e)}
