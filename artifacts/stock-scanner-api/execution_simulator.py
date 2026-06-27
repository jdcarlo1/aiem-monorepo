"""
execution_simulator.py
-------------------------
Models realistic slippage and market impact for PAPER trades, so your
paper-trading track record reflects what would actually happen on a real
fill, not an idealized "got the exact quoted price" assumption.

WHY THIS MATTERS EVEN WITH NO REAL MONEY INVOLVED: this is the single
easiest place for a paper-trading track record to quietly lie to you. If
your shadow_ledger entries assume you always get filled at the exact
mid-price with zero cost, a signal that looks like a 70% win rate on paper
might actually be a 55% win rate once realistic slippage is subtracted —
and that gap is exactly the kind of thing that wouldn't show up until real
money was on the line.

Implements three standard execution models, increasing in realism:

  1. Fixed-spread slippage — simplest: assume you always pay half the
     bid-ask spread on entry and exit.
  2. Volume-participation impact (square-root model) — the standard
     industry approximation: impact scales with the square root of your
     order size as a fraction of typical volume. Same functional form used
     in real market-impact models (Almgren-Chriss and similar).
  3. Execution algorithm simulation (TWAP/VWAP) — splits a paper order
     into smaller slices executed over a time window, reducing modeled
     impact versus a single lump-sum fill.

REQUIRES: numpy, pandas.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional


def fixed_spread_slippage(
    mid_price: float,
    direction: str,
    spread_pct: float = 0.0005,
) -> Dict[str, Any]:
    """Simplest model: you pay half the spread crossing the market.
    spread_pct default of 0.05% is reasonable for a liquid large-cap;
    widen for less liquid tickers."""
    half_spread = mid_price * spread_pct / 2
    fill_price  = mid_price + half_spread if direction == "long" else mid_price - half_spread
    return {
        "model":              "fixed_spread",
        "mid_price":          mid_price,
        "fill_price":         round(fill_price, 4),
        "slippage_cost_pct":  round(spread_pct / 2 * 100, 4),
    }


def sqrt_market_impact(
    mid_price: float,
    direction: str,
    order_shares: float,
    avg_daily_volume: float,
    daily_volatility_pct: float,
    impact_coefficient: float = 1.0,
) -> Dict[str, Any]:
    """Square-root market impact model:
        impact_pct = coefficient * volatility * sqrt(order_size / ADV)
    Same functional form as the Almgren-Chriss family — bigger orders
    relative to typical daily volume cost proportionally more, with
    diminishing (square-root, not linear) severity."""
    if avg_daily_volume <= 0:
        return {"error": "invalid_avg_daily_volume"}

    participation = order_shares / avg_daily_volume
    impact_pct    = impact_coefficient * (daily_volatility_pct / 100) * np.sqrt(participation)
    impact_pct    = min(impact_pct, 0.05)  # cap at 5% — beyond this the model's assumptions break down

    fill_price = (mid_price * (1 + impact_pct) if direction == "long"
                  else mid_price * (1 - impact_pct))

    return {
        "model":              "sqrt_market_impact",
        "mid_price":          mid_price,
        "fill_price":         round(float(fill_price), 4),
        "participation_rate": round(float(participation), 5),
        "impact_pct":         round(float(impact_pct) * 100, 4),
        "warning": (
            "Participation rate above 10% means this order is large relative "
            "to typical volume — the square-root model's assumptions get "
            "shakier at that scale; real execution would need TWAP/VWAP slicing."
            if participation > 0.10 else None
        ),
    }


def simulate_twap_execution(
    intraday_prices: pd.Series,
    direction: str,
    order_shares: float,
    avg_daily_volume: float,
    daily_volatility_pct: float,
    n_slices: int = 10,
) -> Dict[str, Any]:
    """Simulates splitting a paper order into n_slices equal pieces executed
    evenly across the intraday_prices series. Returns the volume-weighted
    average fill price — generally better than a single lump-sum fill for
    any order large enough to matter.

    `intraday_prices` should be a pandas Series of prices sampled across
    the execution window (e.g. 1-minute or 5-minute bars)."""
    if len(intraday_prices) < n_slices:
        return {"error": "insufficient_price_samples",
                "have": len(intraday_prices), "need": n_slices}

    slice_shares    = order_shares / n_slices
    sample_indices  = np.linspace(0, len(intraday_prices) - 1, n_slices).astype(int)
    sampled_prices  = intraday_prices.iloc[sample_indices].values

    fills = []
    for price in sampled_prices:
        impact_result = sqrt_market_impact(
            mid_price=float(price),
            direction=direction,
            order_shares=slice_shares,
            avg_daily_volume=avg_daily_volume,
            daily_volatility_pct=daily_volatility_pct,
        )
        fills.append(impact_result["fill_price"])

    vwap_fill       = float(np.mean(fills))
    naive_lump_sum  = sqrt_market_impact(
        mid_price=float(sampled_prices[0]),
        direction=direction,
        order_shares=order_shares,
        avg_daily_volume=avg_daily_volume,
        daily_volatility_pct=daily_volatility_pct,
    )["fill_price"]

    improvement_pct = (abs(naive_lump_sum - vwap_fill) / naive_lump_sum * 100
                       if naive_lump_sum else 0.0)

    return {
        "model":                             "twap_simulation",
        "n_slices":                          n_slices,
        "slice_fills":                       [round(f, 4) for f in fills],
        "vwap_average_fill":                 round(vwap_fill, 4),
        "naive_lump_sum_fill_for_comparison": round(naive_lump_sum, 4),
        "improvement_vs_lump_sum_pct":       round(improvement_pct, 4),
    }


def apply_execution_realism_to_shadow_trade(
    mid_price_entry: float,
    mid_price_exit: float,
    direction: str,
    order_shares: float,
    avg_daily_volume: float,
    daily_volatility_pct: float,
    spread_pct: float = 0.0005,
) -> Dict[str, Any]:
    """Convenience wrapper: applies BOTH spread and market-impact costs to
    a shadow_ledger-style entry/exit pair, giving you a realistic_return
    to compare against the naive idealized return.

    Use this to adjust shadow_ledger.shadow_performance() results before
    trusting them."""
    entry_spread  = fixed_spread_slippage(mid_price_entry, direction, spread_pct)
    entry_impact  = sqrt_market_impact(
        entry_spread["fill_price"], direction,
        order_shares, avg_daily_volume, daily_volatility_pct,
    )

    exit_direction = "short" if direction == "long" else "long"
    exit_spread   = fixed_spread_slippage(mid_price_exit, exit_direction, spread_pct)
    exit_impact   = sqrt_market_impact(
        exit_spread["fill_price"], exit_direction,
        order_shares, avg_daily_volume, daily_volatility_pct,
    )

    realistic_entry  = entry_impact["fill_price"]
    realistic_exit   = exit_impact["fill_price"]

    if direction == "long":
        naive_return     = (mid_price_exit - mid_price_entry) / mid_price_entry
        realistic_return = (realistic_exit - realistic_entry) / realistic_entry
    else:
        naive_return     = (mid_price_entry - mid_price_exit) / mid_price_entry
        realistic_return = (realistic_entry - realistic_exit) / realistic_entry

    return {
        "naive_idealized_return_pct":  round(naive_return * 100, 4),
        "realistic_return_pct":        round(realistic_return * 100, 4),
        "total_cost_pct":              round((naive_return - realistic_return) * 100, 4),
        "realistic_entry_price":       round(realistic_entry, 4),
        "realistic_exit_price":        round(realistic_exit, 4),
        "note": (
            "Subtract total_cost_pct from your shadow_ledger's reported returns "
            "before trusting a signal's win rate. A signal that barely beats "
            "this cost is not a real edge once execution friction is accounted "
            "for, even in a world where you're not yet risking real money."
        ),
    }


if __name__ == "__main__":
    print("execution_simulator: models realistic slippage/impact for paper trades.")
    print("Use apply_execution_realism_to_shadow_trade() to adjust shadow_ledger results.")
