"""
model_training.py

Trains a regularized classification model on settled-pick outcomes, with a
rule-based baseline always computed alongside it for comparison. Includes
overfitting guardrails: regularization, minimum sample size gate, and
cross-validation rather than a single train/test split.

Falls back to LogisticRegression if xgboost isn't installed.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List

from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False

from feature_engineering import FEATURE_COLUMNS

# Hard floor — do not lower this. Below this many settled outcomes, any
# trained model is more likely to be fitting noise than finding a real edge.
MIN_SAMPLES = 200


@dataclass
class TrainedModel:
    model: object
    feature_columns: List[str]
    cv_auc_mean: float
    cv_auc_std: float
    n_samples: int
    model_type: str
    is_trustworthy: bool  # False if n_samples < MIN_SAMPLES


def build_pipeline(model_type: str = "auto") -> Pipeline:
    """
    model_type: "logistic", "xgboost", or "auto" (xgboost if available,
    else logistic).
    """
    imputer = SimpleImputer(strategy="median")

    if model_type == "auto":
        model_type = "xgboost" if HAS_XGBOOST else "logistic"

    if model_type == "xgboost" and HAS_XGBOOST:
        clf = XGBClassifier(
            n_estimators=100,
            max_depth=3,
            min_child_weight=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="auc",
        )
        return Pipeline([("impute", imputer), ("clf", clf)])

    clf = LogisticRegression(penalty="l2", C=0.5, max_iter=1000)
    return Pipeline([("impute", imputer), ("scale", StandardScaler()), ("clf", clf)])


def train_model(
    df: pd.DataFrame,
    feature_columns: Optional[List[str]] = None,
    model_type: str = "auto",
    n_cv_splits: int = 5,
) -> TrainedModel:
    """
    df must contain feature_columns plus an 'outcome' column (1=win, 0=loss).
    Returns a TrainedModel with cross-validated AUC so you get an honest
    estimate of accuracy rather than one lucky/unlucky split.
    """
    feature_columns = feature_columns or FEATURE_COLUMNS
    n_samples = len(df)

    X = df[feature_columns]
    y = df["outcome"]

    pipeline = build_pipeline(model_type)

    if n_samples < 30:
        pipeline.fit(X, y)
        return TrainedModel(
            model=pipeline,
            feature_columns=feature_columns,
            cv_auc_mean=np.nan,
            cv_auc_std=np.nan,
            n_samples=n_samples,
            model_type=model_type,
            is_trustworthy=False,
        )

    n_splits = min(n_cv_splits, max(2, n_samples // 40))
    tscv = TimeSeriesSplit(n_splits=n_splits)
    cv_scores = cross_val_score(pipeline, X, y, cv=tscv, scoring="roc_auc")

    pipeline.fit(X, y)

    return TrainedModel(
        model=pipeline,
        feature_columns=feature_columns,
        cv_auc_mean=float(np.mean(cv_scores)),
        cv_auc_std=float(np.std(cv_scores)),
        n_samples=n_samples,
        model_type=model_type if model_type != "auto" else ("xgboost" if HAS_XGBOOST else "logistic"),
        is_trustworthy=(n_samples >= MIN_SAMPLES),
    )


def rule_based_baseline_predict(df: pd.DataFrame) -> pd.Series:
    """
    The original hardcoded rule, kept as a baseline for comparison so you
    can see whether the trained model is actually beating it.
    """
    return ((df["rvol"] >= 2) & (df["gap_pct"] >= 1.0)).astype(int)


def get_feature_importance(trained: TrainedModel) -> pd.DataFrame:
    """
    Returns a DataFrame of feature -> importance, sorted descending.
    """
    clf = trained.model.named_steps["clf"]

    if hasattr(clf, "feature_importances_"):
        importances = clf.feature_importances_
    elif hasattr(clf, "coef_"):
        importances = np.abs(clf.coef_[0])
    else:
        raise ValueError("Model type does not expose feature importances")

    return pd.DataFrame({
        "feature": trained.feature_columns,
        "importance": importances,
    }).sort_values("importance", ascending=False).reset_index(drop=True)
