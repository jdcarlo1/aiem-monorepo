"""
position_sizing.py

Once a model produces a calibrated win-rate estimate and you have an
actual payoff ratio (average win size vs average loss size), this
calculates optimal position sizing using the Kelly criterion.

IMPORTANT CONTEXT — read before using:
Full Kelly sizing is famously aggressive and assumes your win-rate
estimate is exactly correct. It isn't — every win rate from this system
has sampling uncertainty (smaller n = more uncertainty), and Kelly sizing
amplifies the cost of being wrong. Industry practice is "fractional Kelly"
(typically 1/4 to 1/2 of full Kelly) specifically to survive estimation
error. This module defaults to 1/4 Kelly and makes you explicitly opt
into anything more aggressive.

This is a money-management tool, not a pattern-finding tool — it should
only be used once a signal/segment has cleared the validation bars in
model_training.py and niche_segment_finder.py (sample size + out-of-
sample confirmation). Sizing confidently around an unvalidated edge is
how small errors become large losses.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass


@dataclass
class KellyResult:
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    payoff_ratio: float
    full_kelly_fraction: float
    recommended_fraction: float
    fractional_kelly_multiplier: float
    warning: str = ""


def calculate_kelly_fraction(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """
    Standard Kelly formula: f* = p - (1-p)/b
    where p = win probability, b = payoff ratio (avg win / avg loss).

    avg_loss_pct should be passed as a positive number (magnitude of the
    average loss), even though losses are negative returns.
    """
    if avg_loss_pct <= 0:
        return 0.0

    b = avg_win_pct / avg_loss_pct
    f_star = win_rate - (1 - win_rate) / b
    return f_star


def kelly_position_size(
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    n_samples: int,
    fractional_multiplier: float = 0.25,
    min_samples_for_full_confidence: int = 200,
) -> KellyResult:
    """
    fractional_multiplier: fraction of full Kelly to actually recommend.
    Default 0.25 (quarter-Kelly). Do not raise above 0.5 without a long
    track record of the win-rate estimate holding steady across multiple
    retraining cycles.

    Below min_samples_for_full_confidence, automatically tightens the
    multiplier further as a safety margin.
    """
    avg_loss_pct = abs(avg_loss_pct)
    full_kelly = calculate_kelly_fraction(win_rate, avg_win_pct, avg_loss_pct)
    payoff_ratio = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else np.nan

    warning = ""
    effective_multiplier = fractional_multiplier

    if n_samples < min_samples_for_full_confidence:
        sample_confidence = min(1.0, n_samples / min_samples_for_full_confidence)
        effective_multiplier = fractional_multiplier * sample_confidence
        warning = (
            f"Only {n_samples} samples (below the {min_samples_for_full_confidence} "
            f"floor used elsewhere in this system). Position size multiplier "
            f"reduced further as a safety margin — treat this win rate as "
            f"provisional, not settled."
        )

    if full_kelly <= 0:
        recommended_fraction = 0.0
        if not warning:
            warning = "Full Kelly is zero or negative — this edge does not justify a position at all."
    else:
        recommended_fraction = max(0.0, full_kelly * effective_multiplier)

    return KellyResult(
        win_rate=win_rate,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        payoff_ratio=payoff_ratio,
        full_kelly_fraction=full_kelly,
        recommended_fraction=recommended_fraction,
        fractional_kelly_multiplier=effective_multiplier,
        warning=warning,
    )


def kelly_from_settled_picks(
    settled_picks_df: pd.DataFrame,
    outcome_col: str = "outcome",
    return_col: str = "return_pct",
    fractional_multiplier: float = 0.25,
) -> KellyResult:
    """
    Convenience wrapper: computes win_rate, avg_win_pct, avg_loss_pct
    directly from a settled-picks DataFrame rather than requiring you to
    calculate them yourself first.
    """
    wins = settled_picks_df[settled_picks_df[outcome_col] == 1]
    losses = settled_picks_df[settled_picks_df[outcome_col] == 0]

    win_rate = len(wins) / len(settled_picks_df) if len(settled_picks_df) > 0 else np.nan
    avg_win_pct = wins[return_col].mean() if len(wins) > 0 else np.nan
    avg_loss_pct = abs(losses[return_col].mean()) if len(losses) > 0 else np.nan

    return kelly_position_size(
        win_rate=win_rate,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        n_samples=len(settled_picks_df),
        fractional_multiplier=fractional_multiplier,
    )
