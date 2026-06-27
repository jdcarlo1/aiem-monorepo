"""
portfolio_allocator.py
-------------------------
Decides how to split paper capital across your MULTIPLE existing signals
(OI Build, Gamma, Charm, Squeeze Fuel, Dark Pool, Float OD, Sweep, Sector
Heat, quant aggregator, plus anything new from signal_discovery_gp) rather
than evaluating each signal in isolation.

This is "more sophisticated" in the sense of doing something genuinely
harder than any single module before it — but it also concentrates risk
in one place if the allocation logic is wrong, so it leans HEAVILY on
methods with a long, boring, well-understood track record rather than
anything novel:

  1. Risk parity — allocate inversely to each signal's recent volatility,
     so one noisy signal can't dominate the portfolio's swings just by
     being noisier, not better.
  2. Kelly-criterion-based sizing (fractional Kelly, capped) — sizes each
     signal's allocation based on its OWN win rate and payoff ratio from
     decision_logger's tracked history, with a hard cap because full Kelly
     is famously aggressive and a single bad estimate can blow up an
     allocation badly.
  3. A correlation penalty — reduces combined allocation to signals that
     are highly correlated with each other (since two "different" signals
     that both fire on the same dark-pool-driven moves aren't actually
     diversifying you).

All of this only ever touches PAPER capital — there is no broker
integration anywhere in this file. It produces a recommended allocation
dict; you (or your paper-trading loop) apply it to simulated capital only.

REQUIRES: numpy, pandas.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional


def risk_parity_weights(volatilities: Dict[str, float]) -> Dict[str, float]:
    """Inverse-volatility weighting: noisier signals get smaller weight."""
    inv_vol = {name: 1.0 / v if v > 1e-9 else 0.0 for name, v in volatilities.items()}
    total   = sum(inv_vol.values())
    if total == 0:
        n = len(volatilities)
        return {name: 1.0 / n for name in volatilities}
    return {name: v / total for name, v in inv_vol.items()}


def fractional_kelly_fraction(
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    kelly_fraction_cap: float = 0.25,
) -> float:
    """Classic Kelly formula: f* = (p*b - q) / b, where b = avg_win/avg_loss.
    Returns FRACTIONAL Kelly (capped, default 25%) because full Kelly
    produces extreme sizes off noisy short-history estimates."""
    if avg_loss_pct <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0
    b          = avg_win_pct / avg_loss_pct
    q          = 1 - win_rate
    full_kelly = max(0.0, (win_rate * b - q) / b) if b > 0 else 0.0
    return min(full_kelly * kelly_fraction_cap, kelly_fraction_cap)


def correlation_penalty_matrix(returns_df: pd.DataFrame) -> pd.DataFrame:
    """Pairwise correlation between signals' historical returns."""
    return returns_df.corr()


def allocate_portfolio(
    signal_stats: Dict[str, Dict[str, float]],
    returns_history: pd.DataFrame,
    total_paper_capital: float,
    max_single_signal_pct: float = 0.30,
    correlation_threshold: float = 0.7,
) -> Dict[str, Any]:
    """Main entry point.

    signal_stats format:
        {
          "dark_pool": {"win_rate": 0.58, "avg_win_pct": 2.1,
                        "avg_loss_pct": 1.4, "volatility": 0.18},
          "gamma":     {"win_rate": 0.52, "avg_win_pct": 1.8,
                        "avg_loss_pct": 1.6, "volatility": 0.22},
        }
    returns_history: DataFrame with one column per signal, daily returns.
    """
    names      = list(signal_stats.keys())
    vols       = {n: signal_stats[n]["volatility"] for n in names}
    rp_weights = risk_parity_weights(vols)

    kelly_weights = {
        n: fractional_kelly_fraction(
            signal_stats[n]["win_rate"],
            signal_stats[n]["avg_win_pct"],
            signal_stats[n]["avg_loss_pct"],
        )
        for n in names
    }

    kelly_total = sum(kelly_weights.values())
    combined = {
        n: 0.5 * rp_weights.get(n, 0)
           + 0.5 * (kelly_weights.get(n, 0) / kelly_total if kelly_total > 0 else 0)
        for n in names
    }

    if not returns_history.empty:
        valid_cols = [c for c in names if c in returns_history.columns]
        if valid_cols:
            corr = correlation_penalty_matrix(returns_history[valid_cols])
            for i, n1 in enumerate(names):
                for n2 in names[i + 1:]:
                    if n1 in corr.columns and n2 in corr.columns:
                        if abs(corr.loc[n1, n2]) >= correlation_threshold:
                            smaller = n1 if combined[n1] < combined[n2] else n2
                            combined[smaller] *= 0.5

    total = sum(combined.values())
    if total > 0:
        combined = {n: v / total for n, v in combined.items()}
    combined = {n: min(v, max_single_signal_pct) for n, v in combined.items()}
    total2 = sum(combined.values())
    if total2 > 0:
        combined = {n: v / total2 for n, v in combined.items()}

    dollar_allocation = {n: round(v * total_paper_capital, 2) for n, v in combined.items()}

    return {
        "total_paper_capital":    total_paper_capital,
        "weights":                {n: round(v, 4) for n, v in combined.items()},
        "dollar_allocation":      dollar_allocation,
        "risk_parity_component":  {n: round(v, 4) for n, v in rp_weights.items()},
        "kelly_component":        {n: round(v, 4) for n, v in kelly_weights.items()},
        "note": (
            "This allocation is computed from your signals' OWN tracked stats — "
            "garbage in, garbage out. If win_rate/avg_win/avg_loss come from a "
            "small or short sample, this allocation is a rough guess dressed up "
            "in formulas, not a precise answer. Recompute weekly as more "
            "decision_logger/shadow_ledger history accumulates, and watch how "
            "much the weights swing — large swings week to week mean your "
            "underlying stats aren't stable enough yet to allocate confidently."
        ),
    }


if __name__ == "__main__":
    print("portfolio_allocator: paper-capital-only allocation across signals.")
    print("Call allocate_portfolio() with signal_stats + returns_history.")
