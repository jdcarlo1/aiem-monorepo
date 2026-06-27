"""
breakout_signature_discovery.py
----------------------------------
Answers exactly the question: "these 5 stocks all ground higher for 5-10
days — were they behaving similarly 1-2 weeks BEFORE that move started?
Is there a repeatable signature I could have caught in advance?"

Pipeline, in order:

  1. identify_breakout_events()    — finds historical 5-10+ day grinding
     uptrends (EXCLUDES single-day gap spikes — a grind and a gap are
     different phenomena with probably different causes)

  2. extract_pre_event_features()  — computes a feature vector using ONLY
     data available up to that point (RSI, volume trend, volatility
     contraction, distance from highs, MA positioning, momentum)

  3. train_breakout_classifier()   — trains on one TIME PERIOD

  4. evaluate_held_out()           — tests on a DIFFERENT period it never saw
     ("I found a pattern that generalizes" vs just "I found a pattern")

  5. scan_for_candidates()         — the 'every morning' function; always
     carries held_out_accuracy alongside the score so you never see a
     probability without knowing how much to trust the model

  run_full_discovery_pipeline()    — convenience wrapper for one-shot
     discovery + validation; use scan_for_candidates() daily after

REQUIRES: pandas, numpy, scikit-learn.
"""

import datetime as dt
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score


def identify_breakout_events(
    price_history: pd.DataFrame,
    min_streak_days: int = 5,
    min_total_gain_pct: float = 8.0,
    max_single_day_gain_pct: float = 6.0,
) -> List[Dict[str, Any]]:
    """Finds historical 'grinding higher for 5-10+ days' events — specifically
    EXCLUDING single-day gap-up spikes (max_single_day_gain_pct cap)."""
    df = price_history.sort_values("date").reset_index(drop=True)
    df["daily_ret_pct"] = df["close"].pct_change() * 100

    events = []
    i = 0
    while i < len(df) - min_streak_days:
        j = i
        cumulative_gain = 0.0
        while j < len(df) - 1:
            daily = df["daily_ret_pct"].iloc[j + 1]
            if pd.isna(daily) or daily > max_single_day_gain_pct or daily < -1.0:
                break
            cumulative_gain += daily
            j += 1
            if j - i >= min_streak_days and cumulative_gain >= min_total_gain_pct:
                events.append({
                    "start_date":     df["date"].iloc[i],
                    "end_date":       df["date"].iloc[j],
                    "start_index":    i,
                    "end_index":      j,
                    "n_days":         j - i,
                    "total_gain_pct": round(cumulative_gain, 2),
                })
                break
        i += 1

    return events


def extract_features_at_index(
    df: pd.DataFrame,
    idx: int,
    volume_col: str = "volume",
    rsi_window: int = 14,
    vol_contraction_window: int = 10,
    momentum_window: int = 5,
) -> Optional[Dict[str, float]]:
    """Computes a feature vector using ONLY data up to and including idx —
    critical that this never looks forward."""
    if idx < 30:
        return None

    window  = df.iloc[max(0, idx - 30):idx + 1].copy()
    closes  = window["close"].values

    # RSI
    deltas   = np.diff(closes)
    gains    = np.where(deltas > 0, deltas, 0)
    losses   = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-rsi_window:]) if len(gains) >= rsi_window else (np.mean(gains) if len(gains) else 0)
    avg_loss = np.mean(losses[-rsi_window:]) if len(losses) >= rsi_window else (np.mean(losses) if len(losses) else 0)
    rsi      = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0

    # Volatility contraction: recent realized vol vs prior realized vol
    log_rets = np.diff(np.log(closes))
    if len(log_rets) >= vol_contraction_window * 2:
        recent_vol = np.std(log_rets[-vol_contraction_window:])
        prior_vol  = np.std(log_rets[-vol_contraction_window * 2:-vol_contraction_window])
        vol_contraction_ratio = recent_vol / prior_vol if prior_vol > 1e-9 else 1.0
    else:
        vol_contraction_ratio = 1.0

    # Distance from recent high
    recent_high              = window["close"].max()
    distance_from_high_pct   = (closes[-1] - recent_high) / recent_high * 100

    # Short-term momentum
    momentum_pct = (
        (closes[-1] - closes[-momentum_window - 1]) / closes[-momentum_window - 1] * 100
        if len(closes) > momentum_window else 0.0
    )

    # Volume trend (slope normalized by mean)
    if volume_col in window.columns and len(window) >= 10:
        vols         = window[volume_col].values
        volume_trend = float(np.polyfit(range(len(vols[-10:])), vols[-10:], 1)[0]) / (np.mean(vols) + 1e-9)
    else:
        volume_trend = 0.0

    # MA positioning
    sma10              = np.mean(closes[-10:]) if len(closes) >= 10 else closes[-1]
    sma20              = np.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]
    price_vs_sma10_pct = (closes[-1] - sma10) / sma10 * 100
    sma10_vs_sma20_pct = (sma10 - sma20) / sma20 * 100

    return {
        "rsi":                    round(float(rsi), 2),
        "vol_contraction_ratio":  round(float(vol_contraction_ratio), 4),
        "distance_from_high_pct": round(float(distance_from_high_pct), 2),
        "momentum_pct":           round(float(momentum_pct), 2),
        "volume_trend":           round(float(volume_trend), 6),
        "price_vs_sma10_pct":     round(float(price_vs_sma10_pct), 2),
        "sma10_vs_sma20_pct":     round(float(sma10_vs_sma20_pct), 2),
    }


FEATURE_NAMES = [
    "rsi", "vol_contraction_ratio", "distance_from_high_pct",
    "momentum_pct", "volume_trend", "price_vs_sma10_pct", "sma10_vs_sma20_pct",
]


def build_labeled_dataset(
    price_histories: Dict[str, pd.DataFrame],
    lookback_days_before_breakout: int = 7,
    min_streak_days: int = 5,
    min_total_gain_pct: float = 8.0,
    negative_sample_ratio: int = 3,
) -> pd.DataFrame:
    """Builds training table across MULTIPLE tickers. For each breakout,
    extracts features from `lookback_days_before_breakout` days BEFORE
    it started (label=1). Negative examples sampled at negative_sample_ratio
    per positive from non-breakout points (label=0)."""
    rows = []
    for ticker, df in price_histories.items():
        df     = df.sort_values("date").reset_index(drop=True)
        events = identify_breakout_events(df, min_streak_days, min_total_gain_pct)
        breakout_start_indices = {e["start_index"] for e in events}

        for event in events:
            feature_idx = event["start_index"] - lookback_days_before_breakout
            features    = extract_features_at_index(df, feature_idx)
            if features:
                rows.append({**features, "ticker": ticker, "label": 1,
                             "event_start": event["start_date"]})

        n_negatives_needed = len(events) * negative_sample_ratio
        candidate_indices  = [
            i for i in range(30, len(df) - 1)
            if not any(
                abs(i - bi) <= lookback_days_before_breakout + min_streak_days
                for bi in breakout_start_indices
            )
        ]
        if candidate_indices and n_negatives_needed > 0:
            chosen = np.random.choice(
                candidate_indices,
                size=min(n_negatives_needed, len(candidate_indices)),
                replace=False,
            )
            for idx in chosen:
                features = extract_features_at_index(df, idx)
                if features:
                    rows.append({**features, "ticker": ticker, "label": 0,
                                 "event_start": df["date"].iloc[idx]})

    return pd.DataFrame(rows)


def train_breakout_classifier(
    labeled_df: pd.DataFrame,
    train_end_date: str,
) -> Dict[str, Any]:
    """Trains ONLY on events before train_end_date. Returns model + feature
    importances — the importances are the direct answer to 'which indicator
    actually distinguished pre-breakout from non-breakout.'"""
    train_df = labeled_df[labeled_df["event_start"] < train_end_date]
    if train_df["label"].nunique() < 2:
        return {"error": "insufficient_class_diversity_in_training_set"}

    X = train_df[FEATURE_NAMES].values
    y = train_df["label"].values

    model = RandomForestClassifier(
        n_estimators=200, max_depth=4, min_samples_leaf=5, random_state=42
    )
    model.fit(X, y)

    importances        = dict(zip(FEATURE_NAMES, model.feature_importances_))
    importances_sorted = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True))

    return {
        "model":                  model,
        "n_training_examples":    len(train_df),
        "n_positive_examples":    int(train_df["label"].sum()),
        "feature_importances":    {k: round(float(v), 4) for k, v in importances_sorted.items()},
        "top_signal":             list(importances_sorted.keys())[0],
    }


def evaluate_held_out(
    model: RandomForestClassifier,
    labeled_df: pd.DataFrame,
    train_end_date: str,
) -> Dict[str, Any]:
    """Tests on events AFTER train_end_date — data the model never saw.
    THIS is the number that answers 'is this accuracy real or did I just
    describe my training data back to myself.'"""
    test_df = labeled_df[labeled_df["event_start"] >= train_end_date]
    if len(test_df) < 10:
        return {"error": "insufficient_held_out_data", "n_available": len(test_df)}

    X_test = test_df[FEATURE_NAMES].values
    y_test = test_df["label"].values
    y_pred = model.predict(X_test)

    return {
        "n_held_out_examples": len(test_df),
        "accuracy":            round(float(accuracy_score(y_test, y_pred)), 4),
        "precision":           round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall":              round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "interpretation": (
            "Precision is the more important number for your use case: of the "
            "candidates the model FLAGS as 'about to break out,' what fraction "
            "actually did? Recall matters less than not chasing false positives."
        ),
    }


def scan_for_candidates(
    model: RandomForestClassifier,
    current_price_histories: Dict[str, pd.DataFrame],
    held_out_accuracy: float,
    probability_threshold: float = 0.6,
) -> List[Dict[str, Any]]:
    """The 'every morning' function. Scores today's data against the
    validated signature. Always carries held_out_accuracy alongside the
    score so you never see a probability without context on the model."""
    candidates = []
    for ticker, df in current_price_histories.items():
        df         = df.sort_values("date").reset_index(drop=True)
        latest_idx = len(df) - 1
        features   = extract_features_at_index(df, latest_idx)
        if features is None:
            continue

        x    = np.array([[features[f] for f in FEATURE_NAMES]])
        prob = float(model.predict_proba(x)[0, 1])

        if prob >= probability_threshold:
            candidates.append({
                "ticker":                 ticker,
                "breakout_probability":   round(prob, 4),
                "model_held_out_accuracy": held_out_accuracy,
                "features_at_scan_time":  features,
                "as_of_date":             str(df["date"].iloc[-1]),
            })

    candidates.sort(key=lambda c: c["breakout_probability"], reverse=True)
    return candidates


def run_full_discovery_pipeline(
    price_histories: Dict[str, pd.DataFrame],
    train_end_date: str,
    lookback_days_before_breakout: int = 7,
) -> Dict[str, Any]:
    """Convenience wrapper: builds dataset, trains, validates OOS, returns
    everything needed to register as a hypothesis and start scanning.
    Run ONCE to discover and validate; use scan_for_candidates() daily."""
    labeled_df = build_labeled_dataset(price_histories, lookback_days_before_breakout)
    if labeled_df.empty:
        return {
            "error":      "no_breakout_events_found",
            "suggestion": "Loosen min_total_gain_pct or min_streak_days.",
        }

    train_result = train_breakout_classifier(labeled_df, train_end_date)
    if "error" in train_result:
        return train_result

    held_out_result = evaluate_held_out(train_result["model"], labeled_df, train_end_date)

    return {
        "feature_importances":  train_result["feature_importances"],
        "top_signal":           train_result["top_signal"],
        "n_training_examples":  train_result["n_training_examples"],
        "held_out_evaluation":  held_out_result,
        "model":                train_result["model"],
        "next_step": (
            "Register this exact feature set + train/test split in hypothesis_registry "
            "BEFORE using scan_for_candidates() going forward, then run the daily scan "
            "output through pre_decision_risk_gate before it reaches your email — a "
            "discovered pattern is a candidate hypothesis, not a guaranteed signal, "
            "even at 70-80% held-out accuracy."
        ),
    }


if __name__ == "__main__":
    print("breakout_signature_discovery: finds, validates, and scans for pre-breakout signatures.")
    print("Run run_full_discovery_pipeline() once, then scan_for_candidates() every morning.")
