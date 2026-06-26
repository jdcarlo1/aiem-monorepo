"""
evaluation_metrics.py

Real performance metrics, not just win rate.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, precision_score, recall_score, brier_score_loss


def classification_metrics(
    y_true: pd.Series, y_pred_proba: pd.Series, threshold: float = 0.5
) -> dict:
    y_pred = (y_pred_proba >= threshold).astype(int)
    return {
        "auc": roc_auc_score(y_true, y_pred_proba) if len(set(y_true)) > 1 else np.nan,
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "n_samples": len(y_true),
    }


def calibration_curve_table(
    y_true: pd.Series, y_pred_proba: pd.Series, n_bins: int = 10
) -> pd.DataFrame:
    """
    Bins predictions by predicted probability and compares to actual outcome
    frequency in each bin. A well-calibrated model has predicted ≈ actual
    in every row.
    """
    df = pd.DataFrame({"y_true": y_true.values, "y_pred_proba": y_pred_proba.values})
    df["bin"] = pd.cut(df["y_pred_proba"], bins=n_bins, labels=False)

    grouped = df.groupby("bin").agg(
        predicted_avg=("y_pred_proba", "mean"),
        actual_rate=("y_true", "mean"),
        n=("y_true", "count"),
    ).reset_index()

    return grouped


def brier_score(y_true: pd.Series, y_pred_proba: pd.Series) -> float:
    """
    Lower is better. A model that always predicts 0.5 gets ~0.25;
    a perfect model gets 0.
    """
    return brier_score_loss(y_true, y_pred_proba)


def precision_at_confidence_threshold(
    y_true: pd.Series,
    y_pred_proba: pd.Series,
    thresholds=(0.5, 0.6, 0.7, 0.8, 0.9),
) -> pd.DataFrame:
    """
    For each confidence threshold, reports: how many predictions cleared it,
    and what the actual win rate was among those.
    """
    rows = []
    for t in thresholds:
        mask = y_pred_proba >= t
        n = mask.sum()
        actual_rate = y_true[mask].mean() if n > 0 else np.nan
        rows.append({"threshold": t, "n_predictions": int(n), "actual_win_rate": actual_rate})
    return pd.DataFrame(rows)


def sharpe_ratio(
    returns_pct: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """
    Annualized Sharpe ratio from a series of per-trade or per-period
    percentage returns.
    """
    excess = returns_pct - (risk_free_rate / periods_per_year)
    if excess.std() == 0 or len(excess) < 2:
        return np.nan
    return float((excess.mean() / excess.std()) * np.sqrt(periods_per_year))


def max_drawdown(cumulative_returns: pd.Series) -> float:
    """
    cumulative_returns should be a running cumulative product of (1 + return)
    over time, i.e. equity curve normalized to start at 1.0.
    """
    running_max = cumulative_returns.cummax()
    drawdown = (cumulative_returns - running_max) / running_max
    return float(drawdown.min())


def full_report(
    y_true: pd.Series,
    y_pred_proba: pd.Series,
    returns_pct: pd.Series = None,
) -> dict:
    """
    Convenience wrapper producing everything you'd want in one retraining
    log entry.
    """
    report = classification_metrics(y_true, y_pred_proba)
    report["brier_score"] = brier_score(y_true, y_pred_proba)
    report["calibration_table"] = calibration_curve_table(y_true, y_pred_proba).to_dict(
        orient="records"
    )
    report["precision_at_threshold"] = precision_at_confidence_threshold(
        y_true, y_pred_proba
    ).to_dict(orient="records")

    if returns_pct is not None:
        cumulative = (1 + returns_pct / 100).cumprod()
        report["sharpe_ratio"] = sharpe_ratio(returns_pct)
        report["max_drawdown"] = max_drawdown(cumulative)

    return report
