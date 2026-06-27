"""
ensemble_combiner.py
-----------------------
Combines your ~9 existing signal layers (and anything new from
signal_discovery_gp) into a single meta-signal, the way a fund's research
team combines many independent sub-signals rather than trading each one in
isolation.

Three combination methods, in increasing sophistication:

  1. Simple weighted average — baseline, weights from each signal's
     historical win rate. Always compute this even when using the fancier
     methods below, as your sanity-check baseline.

  2. Stacking (meta-learner) — trains a SECOND, simple model (logistic
     regression) whose INPUTS are your signals' individual outputs, and
     whose job is to learn how to best combine them. This is the standard
     "ensemble of ensembles" pattern used in quant research and Kaggle-style
     competitions alike.

  3. Regime-conditional weighting — learns DIFFERENT combination weights
     for different market regimes (using whatever regime label your
     existing HMM layer or regime_monitor.py produces), since a signal that
     works well in low-vol grinding markets may be actively harmful in a
     volatility spike, and vice versa.

CRITICAL DISCIPLINE: the stacking meta-learner is itself a model that can
overfit just like any single signal. It MUST be trained on one period and
validated on a held-out period it never saw, exactly like everything else
in this package — there is no exemption for "it's just combining other
signals so it's safer." Combining several overfit signals with an overfit
combiner produces a confidently overfit result.

REQUIRES: numpy, pandas, scikit-learn.
"""

import json
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split


def simple_weighted_average(
    signal_scores: Dict[str, float],
    signal_win_rates: Dict[str, float],
) -> Dict[str, Any]:
    """Baseline combiner: weight each signal's current score by its
    historical win rate. Always compute this as your sanity-check floor."""
    total_weight = sum(signal_win_rates.values())
    if total_weight == 0:
        weights = {k: 1.0 / len(signal_scores) for k in signal_scores}
    else:
        weights = {k: signal_win_rates.get(k, 0) / total_weight for k in signal_scores}

    combined_score = sum(signal_scores[k] * weights.get(k, 0) for k in signal_scores)
    return {
        "method":         "simple_weighted_average",
        "combined_score": round(combined_score, 4),
        "weights_used":   {k: round(v, 4) for k, v in weights.items()},
    }


class StackingEnsemble:
    """Meta-learner: takes each signal's score as a feature, predicts
    whether forward return was positive. The learned coefficients
    (model.coef_) are fully inspectable — read them periodically."""

    def __init__(self):
        self.model        = LogisticRegression(max_iter=1000)
        self.signal_names: List[str] = []
        self.is_fitted    = False

    def fit(self, train_df: pd.DataFrame, signal_columns: List[str], outcome_column: str = "was_winner"):
        """Fit on training split only — use fit_and_validate_stacking() for
        the proper train/holdout pattern."""
        self.signal_names = signal_columns
        X = train_df[signal_columns].values
        y = train_df[outcome_column].values
        self.model.fit(X, y)
        self.is_fitted = True

    def predict_proba(self, signal_scores: Dict[str, float]) -> float:
        if not self.is_fitted:
            raise RuntimeError("Call fit() before predict_proba().")
        x = np.array([[signal_scores.get(name, 0.0) for name in self.signal_names]])
        return float(self.model.predict_proba(x)[0, 1])

    def get_learned_weights(self) -> Dict[str, float]:
        """The actual coefficients assigned to each signal — the inspectable
        'why' behind the combined output."""
        if not self.is_fitted:
            return {}
        return {name: round(float(coef), 4) for name, coef in zip(self.signal_names, self.model.coef_[0])}


def fit_and_validate_stacking(
    full_df: pd.DataFrame,
    signal_columns: List[str],
    outcome_column: str = "was_winner",
    test_size: float = 0.3,
    random_state: int = 42,
) -> Dict[str, Any]:
    """Proper train/holdout pattern for the stacking ensemble. Returns the
    fitted ensemble PLUS its held-out accuracy so you can judge whether
    combination adds value over simple average before trusting it.

    shuffle=False preserves time order — never shuffle time-series splits,
    it leaks future information into training."""
    train_df, test_df = train_test_split(
        full_df, test_size=test_size, random_state=random_state, shuffle=False
    )

    ensemble = StackingEnsemble()
    ensemble.fit(train_df, signal_columns, outcome_column)

    X_test              = test_df[signal_columns].values
    y_test              = test_df[outcome_column].values
    held_out_accuracy   = float(ensemble.model.score(X_test, y_test))

    baseline_preds = (
        test_df[signal_columns].mean(axis=1) > test_df[signal_columns].mean(axis=1).median()
    ).astype(int)
    baseline_accuracy = float((baseline_preds.values == y_test).mean())

    return {
        "ensemble":             ensemble,
        "held_out_accuracy":    round(held_out_accuracy, 4),
        "baseline_accuracy":    round(baseline_accuracy, 4),
        "stacking_beat_baseline": held_out_accuracy > baseline_accuracy,
        "learned_weights":      ensemble.get_learned_weights(),
        "note": (
            "If stacking_beat_baseline is False, the added complexity of a "
            "meta-learner isn't earning its keep — use simple_weighted_average "
            "instead. More sophisticated isn't automatically better; it has to "
            "prove it on held-out data, same as everything else in this package."
        ),
    }


class RegimeConditionalEnsemble:
    """Learns SEPARATE stacking ensembles for each regime label (from your
    HMM layer or regime_monitor.py). At prediction time, uses the
    sub-model matching the current regime."""

    def __init__(self):
        self.models_by_regime: Dict[str, StackingEnsemble] = {}

    def fit(self, train_df: pd.DataFrame, signal_columns: List[str],
            regime_column: str, outcome_column: str = "was_winner"):
        for regime in train_df[regime_column].unique():
            subset = train_df[train_df[regime_column] == regime]
            if len(subset) < 30:
                continue
            model = StackingEnsemble()
            model.fit(subset, signal_columns, outcome_column)
            self.models_by_regime[regime] = model

    def predict_proba(self, signal_scores: Dict[str, float], current_regime: str) -> Dict[str, Any]:
        if current_regime not in self.models_by_regime:
            return {
                "error":             "no_model_for_regime",
                "current_regime":    current_regime,
                "available_regimes": list(self.models_by_regime.keys()),
                "fallback":          "Use simple_weighted_average() instead for this regime.",
            }
        model = self.models_by_regime[current_regime]
        return {
            "current_regime":          current_regime,
            "combined_probability":    round(model.predict_proba(signal_scores), 4),
            "regime_specific_weights": model.get_learned_weights(),
        }


if __name__ == "__main__":
    print("ensemble_combiner: simple_weighted_average() is always your baseline.")
    print("Use fit_and_validate_stacking() and only trust it if stacking_beat_baseline=True.")
