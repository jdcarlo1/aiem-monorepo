"""
intraday_continuation_scanner.py
------------------------------------
Answers: "this stock behaved a certain way DURING today's session — close
near the highs, strong afternoon volume, higher lows all day — does that
pattern historically predict it keeps going UP TOMORROW, or does it tend
to fade?"

This is a different question than premarket_gap_continuation_scanner (premarket
to open behavior) and breakout_signature_discovery (multi-day grinding patterns).
This one is about HOW a single session traded — the shape of the day, not just
the net result — since two stocks can both close +3% with very different
underlying behavior (one grinding up all day on rising volume, one spiking at
open then fading before a late recovery), and those patterns predict different
things about tomorrow.

7 intraday shape features:
  1. close_position_in_range      — (close - low) / (high - low); 1.0 = closed at high
  2. afternoon_morning_volume_ratio — was buying building or fading through the day?
  3. higher_lows_count            — buyers stepping in on dips during the session
  4. closing_range_trend_3day     — slope of close_position over prior 3 days (building vs one-off)
  5. relative_volume_vs_30day_avg — strong close on thin volume is weaker evidence
  6. day_total_return_pct         — net close-to-close change
  7. gap_at_open_pct              — how much of the move happened right at the open

REQUIRES: pandas, numpy, scikit-learn.
"""

from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score

import decision_logger as dl


FEATURE_NAMES = [
    "close_position_in_range",        # (close - low) / (high - low), 0-1
    "afternoon_morning_volume_ratio",  # afternoon vol / morning vol
    "higher_lows_count",               # local lows higher than prior local low
    "closing_range_trend_3day",        # slope of close_position over last 3 days
    "relative_volume_vs_30day_avg",    # today's total vol / 30d avg daily vol
    "day_total_return_pct",            # close-to-close net change %
    "gap_at_open_pct",                 # open vs prior close %
]


def compute_intraday_features(
    intraday_bars: pd.DataFrame,
    daily_history: pd.DataFrame,
    avg_volume_30d: float,
    closing_range_trend_3day: float = 0.0,
) -> Optional[Dict[str, float]]:
    """Compute the 7-feature vector for one completed trading session.
    intraday_bars: minute or 5-min bars for the day, columns: [time, high, low, close, volume].
    daily_history: prior days' daily OHLCV (most recent row = yesterday's close).
    avg_volume_30d: 30-day average daily volume for normalisation.
    closing_range_trend_3day: pass pre-computed slope if available, or 0.0 (flat) if not.
    Returns None if intraday_bars has fewer than 10 rows."""
    if len(intraday_bars) < 10:
        return None

    day_high  = intraday_bars["high"].max()
    day_low   = intraday_bars["low"].min()
    day_close = intraday_bars["close"].iloc[-1]
    day_open  = intraday_bars["close"].iloc[0]

    close_position_in_range = (
        (day_close - day_low) / (day_high - day_low)
        if day_high > day_low else 0.5
    )

    midpoint          = len(intraday_bars) // 2
    morning_volume    = intraday_bars["volume"].iloc[:midpoint].sum()
    afternoon_volume  = intraday_bars["volume"].iloc[midpoint:].sum()
    afternoon_morning_volume_ratio = afternoon_volume / morning_volume if morning_volume > 0 else 1.0

    lows       = intraday_bars["low"].values
    local_lows = []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            local_lows.append(lows[i])
    higher_lows_count = sum(
        1 for i in range(1, len(local_lows)) if local_lows[i] > local_lows[i - 1]
    )

    prev_close = daily_history["close"].iloc[-1] if len(daily_history) else None
    day_total_return_pct = (
        (day_close - prev_close) / prev_close * 100 if prev_close else 0.0
    )
    gap_at_open_pct = (
        (day_open - prev_close) / prev_close * 100 if prev_close else 0.0
    )

    relative_volume_vs_30day_avg = (
        intraday_bars["volume"].sum() / avg_volume_30d if avg_volume_30d > 0 else 1.0
    )

    return {
        "close_position_in_range":        round(float(close_position_in_range), 4),
        "afternoon_morning_volume_ratio":  round(float(afternoon_morning_volume_ratio), 4),
        "higher_lows_count":              int(higher_lows_count),
        "closing_range_trend_3day":       round(float(closing_range_trend_3day), 4),
        "relative_volume_vs_30day_avg":   round(float(relative_volume_vs_30day_avg), 4),
        "day_total_return_pct":           round(float(day_total_return_pct), 2),
        "gap_at_open_pct":                round(float(gap_at_open_pct), 2),
    }


def build_labeled_dataset(daily_features_with_outcomes: pd.DataFrame) -> pd.DataFrame:
    """Validates all FEATURE_NAMES columns are present. Expects 'next_day_continued'
    (1 if next day's close-to-close return exceeded the continuation threshold, else 0)
    and 'date' for the time-based train/test split."""
    missing = [f for f in FEATURE_NAMES if f not in daily_features_with_outcomes.columns]
    if missing:
        raise ValueError(f"Missing required feature columns: {missing}")
    return daily_features_with_outcomes


def train_continuation_classifier(
    labeled_df: pd.DataFrame,
    train_end_date: str,
) -> Dict[str, Any]:
    """Trains ONLY on events before train_end_date."""
    train_df = labeled_df[labeled_df["date"] < train_end_date]
    if train_df["next_day_continued"].nunique() < 2:
        return {"error": "insufficient_class_diversity_in_training_set"}

    X = train_df[FEATURE_NAMES].values
    y = train_df["next_day_continued"].values

    model = RandomForestClassifier(
        n_estimators=200, max_depth=4, min_samples_leaf=8, random_state=42
    )
    model.fit(X, y)

    importances        = dict(zip(FEATURE_NAMES, model.feature_importances_))
    importances_sorted = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True))

    return {
        "model":                  model,
        "n_training_examples":    len(train_df),
        "n_positive":             int(train_df["next_day_continued"].sum()),
        "feature_importances":    {k: round(float(v), 4) for k, v in importances_sorted.items()},
        "top_signal":             list(importances_sorted.keys())[0],
    }


def evaluate_held_out(
    model: RandomForestClassifier,
    labeled_df: pd.DataFrame,
    train_end_date: str,
) -> Dict[str, Any]:
    """Tests on events AFTER train_end_date. Reports precision at >=0.70
    confidence — the bar you'd actually act on for a next-day signal."""
    test_df = labeled_df[labeled_df["date"] >= train_end_date]
    if len(test_df) < 10:
        return {"error": "insufficient_held_out_data", "n_available": len(test_df)}

    X_test  = test_df[FEATURE_NAMES].values
    y_test  = test_df["next_day_continued"].values
    y_pred  = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    high_conf_mask      = y_proba >= 0.70
    high_conf_precision = (
        float(precision_score(y_test[high_conf_mask], y_pred[high_conf_mask], zero_division=0))
        if high_conf_mask.sum() > 0 else None
    )

    return {
        "n_held_out_examples":            len(test_df),
        "overall_accuracy":               round(float(accuracy_score(y_test, y_pred)), 4),
        "overall_precision":              round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "overall_recall":                 round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "n_high_confidence_predictions":  int(high_conf_mask.sum()),
        "precision_at_70pct_confidence":  round(high_conf_precision, 4) if high_conf_precision is not None else None,
    }


def scan_end_of_day_candidates(
    model: RandomForestClassifier,
    today_features_by_ticker: Dict[str, Dict[str, float]],
    held_out_precision: Optional[float],
    probability_threshold: float = 0.65,
) -> List[Dict[str, Any]]:
    """Run after market close. today_features_by_ticker is
    {ticker: {feature_name: value}} computed from today's completed session.
    Returns ranked candidates for tomorrow, each with the held-out precision
    attached so you always know how much to trust the probability score."""
    candidates = []
    for ticker, features in today_features_by_ticker.items():
        if any(f not in features for f in FEATURE_NAMES):
            continue

        x    = np.array([[features[f] for f in FEATURE_NAMES]])
        prob = float(model.predict_proba(x)[0, 1])

        if prob >= probability_threshold:
            candidates.append({
                "ticker":                             ticker,
                "next_day_continuation_probability":  round(prob, 4),
                "model_held_out_precision":           held_out_precision,
                "todays_features":                    features,
            })

    candidates.sort(key=lambda c: c["next_day_continuation_probability"], reverse=True)

    for c in candidates:
        dl.log_decision(
            signal_name   = "intraday_continuation_scanner",
            ticker        = c["ticker"],
            decision_type = "trade",
            reasoning     = (
                f"Intraday behavior model: {c['next_day_continuation_probability']:.1%} probability "
                f"of continuation tomorrow. Close position in range: "
                f"{c['todays_features'].get('close_position_in_range', 'n/a')}. "
                f"Held-out precision at this confidence tier: {held_out_precision}."
            ),
            confidence           = c["next_day_continuation_probability"],
            input_state_snapshot = c,
        )

    return candidates


if __name__ == "__main__":
    print("intraday_continuation_scanner: analyzes session SHAPE (not just net change) to predict tomorrow.")
    print(f"Required features: {FEATURE_NAMES}")
    print("Train once, then scan_end_of_day_candidates() after each market close.")
