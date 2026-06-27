"""
premarket_gap_continuation_scanner.py
----------------------------------------
Answers: "a stock is up 2-3% premarket — what historically tells us whether
that gap CONTINUES into a much bigger intraday move, vs fades back down by
midday?"

Includes an explicit SHORT SQUEEZE sub-score, since squeeze dynamics have
a well-understood, specific signature (high short interest + low float +
high relative volume + already moving against shorts) that's worth scoring
separately — a squeeze-driven gap and a news-driven gap behave differently
and deserve different position sizing, not one blended probability.

HONEST LIMITATION: premarket data is THIN. Low volume means noisy, unreliable
price action — a stock "up 3%" on 4,000 shares is a much weaker signal than
the same gap on 2 million shares. Every function treats premarket volume as a
primary input, not an afterthought. min_premarket_volume_ratio is a hard floor
that filters before the model even runs.

Pipeline:
  1. identify_gap_events()           — labels historical gaps as continued/faded
  2. build_labeled_gap_dataset()     — validates feature columns present
  3. short_squeeze_subscore()        — rule-based squeeze ingredients check
  4. train_continuation_classifier() — trained on one time period
  5. evaluate_held_out()             — tested on a DIFFERENT period
  6. score_premarket_candidates()    — the 'every morning' function

REQUIRES: pandas, numpy, scikit-learn.
"""

from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score

import decision_logger as dl


FEATURE_NAMES = [
    "premarket_volume_ratio",      # premarket volume / 30-day avg daily volume
    "gap_pct",                     # premarket gap size at scan time
    "premarket_range_pct",         # (premarket high - premarket low) / premarket low
    "relative_volume_30d",         # today's volume-so-far vs typical at this time of day
    "short_interest_pct_float",    # % of float currently short
    "days_to_cover",               # short interest / avg daily volume
    "float_shares_millions",       # smaller float = more squeeze-prone
    "distance_from_52wk_high_pct", # near highs = different dynamic than deep in downtrend
]


def identify_gap_events(
    intraday_df: pd.DataFrame,
    min_gap_pct: float = 2.0,
    continuation_threshold_pct: float = 5.0,
) -> List[Dict[str, Any]]:
    """intraday_df must have: date, premarket_close, open, midday_price.
    Labels each gap event as continued (1) if price moved at least
    continuation_threshold_pct further from open by midday in the same
    direction as the gap; faded (0) otherwise."""
    events = []
    for _, row in intraday_df.iterrows():
        gap_pct = (row["open"] - row["premarket_close"]) / row["premarket_close"] * 100
        if abs(gap_pct) < min_gap_pct:
            continue

        further_move_pct = (row["midday_price"] - row["open"]) / row["open"] * 100
        continued = (
            (gap_pct > 0 and further_move_pct >= continuation_threshold_pct) or
            (gap_pct < 0 and further_move_pct <= -continuation_threshold_pct)
        )
        events.append({
            "date":             row["date"],
            "ticker":           row.get("ticker", "unknown"),
            "gap_pct":          round(float(gap_pct), 2),
            "further_move_pct": round(float(further_move_pct), 2),
            "continued":        int(continued),
        })
    return events


def build_labeled_gap_dataset(gap_events_with_features: pd.DataFrame) -> pd.DataFrame:
    """Validates all FEATURE_NAMES columns are present. Feature assembly is
    left to the caller because premarket data (short interest, float) often
    comes from different sources than price history."""
    missing = [f for f in FEATURE_NAMES if f not in gap_events_with_features.columns]
    if missing:
        raise ValueError(f"Missing required feature columns: {missing}")
    return gap_events_with_features


def short_squeeze_subscore(features: Dict[str, float]) -> Dict[str, Any]:
    """Rule-based squeeze ingredients check — separate from the general
    continuation model because a squeeze-driven gap and a news-driven gap
    deserve different analysis. Classic squeeze ingredients: high SI % of
    float, high days-to-cover, small float, and already moving against
    shorts on elevated relative volume."""
    si_pct    = features.get("short_interest_pct_float", 0)
    dtc       = features.get("days_to_cover", 0)
    float_m   = features.get("float_shares_millions", 1e9)
    rel_vol   = features.get("relative_volume_30d", 1.0)
    gap       = features.get("gap_pct", 0)

    score   = 0
    reasons = []

    if si_pct >= 20:
        score += 2; reasons.append(f"Short interest {si_pct:.1f}% of float is high")
    elif si_pct >= 10:
        score += 1; reasons.append(f"Short interest {si_pct:.1f}% of float is elevated")

    if dtc >= 5:
        score += 2; reasons.append(f"Days-to-cover {dtc:.1f} means shorts can't exit quickly")
    elif dtc >= 2:
        score += 1

    if float_m <= 20:
        score += 2; reasons.append(f"Float of {float_m:.1f}M shares is small — less supply to absorb buying")
    elif float_m <= 50:
        score += 1

    if rel_vol >= 3 and gap > 0:
        score += 2; reasons.append(f"Relative volume {rel_vol:.1f}x with positive gap — possible forced buying underway")

    squeeze_likelihood = "high" if score >= 6 else "moderate" if score >= 3 else "low"
    return {
        "squeeze_score":     score,
        "squeeze_score_max": 8,
        "squeeze_likelihood": squeeze_likelihood,
        "reasons":           reasons,
        "caveat": (
            "A high squeeze score means the INGREDIENTS for a squeeze are present, "
            "not that one is happening or will happen today. Squeezes are famously "
            "hard to time — this score is a flag to look closer, not a standalone "
            "trade signal."
        ),
    }


def train_continuation_classifier(
    labeled_df: pd.DataFrame,
    train_end_date: str,
) -> Dict[str, Any]:
    """Trains ONLY on events before train_end_date."""
    train_df = labeled_df[labeled_df["date"] < train_end_date]
    if train_df["continued"].nunique() < 2:
        return {"error": "insufficient_class_diversity_in_training_set"}

    X = train_df[FEATURE_NAMES].values
    y = train_df["continued"].values

    model = GradientBoostingClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.05, random_state=42
    )
    model.fit(X, y)

    importances        = dict(zip(FEATURE_NAMES, model.feature_importances_))
    importances_sorted = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True))

    return {
        "model":                model,
        "n_training_examples":  len(train_df),
        "n_continued":          int(train_df["continued"].sum()),
        "feature_importances":  {k: round(float(v), 4) for k, v in importances_sorted.items()},
    }


def evaluate_held_out(
    model: GradientBoostingClassifier,
    labeled_df: pd.DataFrame,
    train_end_date: str,
) -> Dict[str, Any]:
    """Tests on events AFTER train_end_date. Also computes precision
    specifically at the >=0.8 confidence threshold — that is the bar
    you'd actually act on."""
    test_df = labeled_df[labeled_df["date"] >= train_end_date]
    if len(test_df) < 10:
        return {"error": "insufficient_held_out_data", "n_available": len(test_df)}

    X_test  = test_df[FEATURE_NAMES].values
    y_test  = test_df["continued"].values
    y_pred  = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    high_conf_mask      = y_proba >= 0.8
    high_conf_precision = (
        float(precision_score(y_test[high_conf_mask], y_pred[high_conf_mask], zero_division=0))
        if high_conf_mask.sum() > 0 else None
    )

    return {
        "n_held_out_examples":                    len(test_df),
        "overall_accuracy":                       round(float(accuracy_score(y_test, y_pred)), 4),
        "overall_precision":                      round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "overall_recall":                         round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "n_high_confidence_predictions":          int(high_conf_mask.sum()),
        "precision_at_80pct_confidence_threshold": round(high_conf_precision, 4) if high_conf_precision is not None else None,
        "interpretation": (
            "precision_at_80pct_confidence_threshold answers your real question: "
            "of the times the model said 80%+ confident, how often was it right? "
            "If this is meaningfully BELOW 80% on held-out data, the model's "
            "confidence is miscalibrated — distrust high-confidence scores until "
            "more held-out data accumulates."
            if high_conf_precision is not None else
            "No predictions reached the 80% confidence threshold on held-out data — "
            "the model isn't finding anything it's that confident about yet."
        ),
    }


def score_premarket_candidates(
    model: GradientBoostingClassifier,
    current_premarket_features: Dict[str, Dict[str, float]],
    held_out_precision_at_80: Optional[float],
    probability_threshold: float = 0.8,
    min_premarket_volume_ratio: float = 1.5,
) -> List[Dict[str, Any]]:
    """The 'every morning' function. current_premarket_features is
    {ticker: {feature_name: value}}. Filters out anything below
    min_premarket_volume_ratio FIRST — thin premarket volume makes
    every other feature unreliable, so this is a hard floor."""
    candidates = []
    for ticker, features in current_premarket_features.items():
        if features.get("premarket_volume_ratio", 0) < min_premarket_volume_ratio:
            continue
        if any(f not in features for f in FEATURE_NAMES):
            continue

        x    = np.array([[features[f] for f in FEATURE_NAMES]])
        prob = float(model.predict_proba(x)[0, 1])

        if prob >= probability_threshold:
            squeeze_info = short_squeeze_subscore(features)
            candidates.append({
                "ticker":                                    ticker,
                "continuation_probability":                  round(prob, 4),
                "held_out_precision_at_this_confidence":     held_out_precision_at_80,
                "squeeze_subscore":                          squeeze_info,
                "features":                                  features,
            })

    candidates.sort(key=lambda c: c["continuation_probability"], reverse=True)

    for c in candidates:
        dl.log_decision(
            signal_name  = "premarket_gap_continuation",
            ticker       = c["ticker"],
            decision_type = "trade",
            reasoning    = (
                f"Premarket gap continuation model: {c['continuation_probability']:.1%} probability. "
                f"Held-out precision at this confidence level: {held_out_precision_at_80}. "
                f"Squeeze subscore: {c['squeeze_subscore']['squeeze_likelihood']} "
                f"({c['squeeze_subscore']['squeeze_score']}/{c['squeeze_subscore']['squeeze_score_max']})."
            ),
            confidence          = c["continuation_probability"],
            input_state_snapshot = c,
        )

    return candidates


if __name__ == "__main__":
    print("premarket_gap_continuation_scanner: scores live premarket gaps against a validated historical signature.")
    print(f"Required features: {FEATURE_NAMES}")
    print("Train once, then score_premarket_candidates() every morning.")
