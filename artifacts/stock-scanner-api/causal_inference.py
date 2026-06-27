"""
causal_inference.py
---------------------
Attempts to distinguish "this signal actually causes/precedes the price move
in a structural way" from "this signal happens to correlate with it because
both are driven by something else."

HONEST LIMITATION: Real causal inference requires domain assumptions that are
easy to get subtly wrong. This module implements two well-understood, explainable
techniques — a STRUCTURED WAY TO ASK THE QUESTION, not a guarantee.

Techniques:
  1. Granger-style precedence test — does the signal's PAST values help predict
     future returns beyond what past returns alone predict?
  2. Confound control via residualization — regress out a known confound from
     BOTH the signal and forward return, then check if correlation survives.

REQUIRES: numpy, pandas, statsmodels.
"""

import json
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

try:
    import statsmodels.api as sm
    from statsmodels.tsa.stattools import grangercausalitytests
    _HAS_STATSMODELS = True
except ImportError:
    _HAS_STATSMODELS = False


def granger_precedence_test(
    signal_series: pd.Series,
    forward_return_series: pd.Series,
    max_lag: int = 5,
) -> Dict[str, Any]:
    """Tests whether past values of the signal help predict forward returns
    beyond what past returns alone already predict.

    Note: tests PRECEDENCE/predictive value, not true causation.
    """
    if not _HAS_STATSMODELS:
        return {"error": "statsmodels not installed", "detail": "pip install statsmodels"}

    df = pd.DataFrame({"signal": signal_series, "fwd_ret": forward_return_series}).dropna()
    if len(df) < max_lag * 10:
        return {"error": "insufficient_data", "n_rows": len(df), "min_required": max_lag * 10}

    try:
        result = grangercausalitytests(df[["fwd_ret", "signal"]], maxlag=max_lag, verbose=False)
    except Exception as e:
        return {"error": "granger_test_failed", "detail": str(e)}

    p_values  = {lag: round(float(res[0]["ssr_ftest"][1]), 5) for lag, res in result.items()}
    best_lag  = min(p_values, key=p_values.get)

    return {
        "p_values_by_lag":   p_values,
        "best_lag":          best_lag,
        "best_p_value":      p_values[best_lag],
        "interpretation": (
            "A low p-value (<0.05) at some lag suggests the signal's past values carry "
            "predictive information about forward returns beyond what past returns alone "
            "explain. This is evidence of predictive PRECEDENCE, NOT proof of causation."
        ),
        "caveat": (
            "Granger tests assume linear relationships and stationarity. Run on "
            "stationary-transformed series (returns, not raw prices) and treat results "
            "as suggestive, not conclusive."
        ),
    }


def confound_residualized_correlation(
    signal_series: pd.Series,
    forward_return_series: pd.Series,
    confound_df: pd.DataFrame,
    confound_names: List[str],
) -> Dict[str, Any]:
    """Regresses named confounds out of BOTH signal and forward return, then
    checks whether their correlation survives in the residuals.

    If raw correlation is strong but RESIDUAL correlation collapses toward zero,
    the original relationship was likely explained by the confound(s).
    """
    if not _HAS_STATSMODELS:
        return {"error": "statsmodels not installed", "detail": "pip install statsmodels"}

    df = pd.concat(
        [signal_series.rename("signal"), forward_return_series.rename("fwd_ret"),
         confound_df[confound_names]],
        axis=1,
    ).dropna()

    if len(df) < 30:
        return {"error": "insufficient_data", "n_rows": len(df)}

    X = sm.add_constant(df[confound_names])

    signal_resid = sm.OLS(df["signal"],  X).fit().resid
    fwd_resid    = sm.OLS(df["fwd_ret"], X).fit().resid

    raw_corr   = float(df["signal"].corr(df["fwd_ret"]))
    resid_corr = float(np.corrcoef(signal_resid, fwd_resid)[0, 1])

    explained_away_ratio = (
        1 - (abs(resid_corr) / abs(raw_corr)) if raw_corr != 0 else None
    )
    survived = explained_away_ratio is not None and explained_away_ratio < 0.4

    return {
        "confounds_controlled_for":               confound_names,
        "raw_correlation":                        round(raw_corr, 4),
        "residual_correlation_after_controlling": round(resid_corr, 4),
        "explained_away_ratio":                   round(explained_away_ratio, 4) if explained_away_ratio is not None else None,
        "interpretation": (
            f"After controlling for {confound_names}, the correlation "
            f"{'mostly survived' if survived else 'was substantially reduced or reversed'} "
            f"— {'the signal likely carries information beyond these confounds.' if survived else 'these confounds may explain most of the apparent relationship.'}"
        ),
    }


def run_causal_review(
    signal_series: pd.Series,
    forward_return_series: pd.Series,
    confound_df: Optional[pd.DataFrame] = None,
    confound_names: Optional[List[str]] = None,
    max_lag: int = 5,
) -> Dict[str, Any]:
    """Convenience wrapper: runs both checks and returns a combined report."""
    granger = granger_precedence_test(signal_series, forward_return_series, max_lag)

    confound_result = None
    if confound_df is not None and confound_names:
        confound_result = confound_residualized_correlation(
            signal_series, forward_return_series, confound_df, confound_names
        )

    return {
        "granger_precedence":      granger,
        "confound_residualization": confound_result,
        "overall_note": (
            "Neither test here proves causation. Strong precedence AND a relationship "
            "that survives confound control is meaningfully more reassuring than either "
            "alone, but unmeasured confounders not in confound_names can never be fully "
            "ruled out with observational data."
        ),
    }


if __name__ == "__main__":
    print("causal_inference: requires statsmodels. pip install statsmodels")
    print("Use run_causal_review() for combined precedence + confound-control report.")
