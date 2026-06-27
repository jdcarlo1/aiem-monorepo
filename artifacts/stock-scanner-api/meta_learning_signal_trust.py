"""
meta_learning_signal_trust.py
---------------------------------
You have multiple scanners now: breakout_signature_discovery,
premarket_gap_continuation_scanner, intraday_continuation_scanner,
smart_money_divergence_detector. This module learns, FROM ACTUAL TRACKED
OUTCOMES (via decision_logger), which of these signals is actually
performing well RIGHT NOW, and in WHICH market context (regime, volatility
tier) — then automatically adjusts how much weight each signal gets,
instead of you manually deciding "trust the breakout scanner more this
month" by feel.

This is the genuinely useful version of "meta-learning" for your situation:
not a fancier model architecture, but a system that watches its OWN
sub-systems' track records and shifts trust toward whichever is actually
earning it, conditioned on context (a signal that's great in calm markets
but terrible in volatile ones should get downweighted specifically during
volatile stretches, not blanket-trusted or blanket-distrusted).

Design, kept deliberately simple and fully inspectable (this is NOT a
black box — every weight is a number you can read and explain):

  1. Pulls each signal's recent outcomes from decision_logger (which
     already tracks reasoning + outcome_return for every decision).
  2. Computes a rolling, EXPONENTIALLY-DECAYED win rate per signal, per
     context bucket (e.g. "breakout_signature in calm_market" vs
     "breakout_signature in volatile_market" are tracked SEPARATELY).
     Decay means recent performance matters more than performance from
     months ago — a signal that worked well last quarter but has gone
     cold recently should lose trust gradually, not be stuck on an old
     average forever.
  3. Converts rolling win rates into TRUST WEIGHTS that
     ensemble_combiner.py or pre_decision_risk_gate.py can use to
     up-weight or down-weight each signal's contribution.
  4. Updates continuously (every time a new outcome comes in) — this is
     the one piece of "automatic improvement" in the whole package that
     doesn't need a manual retrain step, because it's adjusting WEIGHTS
     on existing validated signals, not training a new model from scratch.
     The underlying signals themselves still go through
     automated_retrain_pipeline's human-gated process when actually retrained.

REQUIRES: AIEM_DATABASE_URL.
"""

import os
import json
import datetime as dt
from typing import Dict, Any, List, Optional

import numpy as np
import psycopg2
import psycopg2.extras

import decision_logger as dl


DDL = """
CREATE TABLE IF NOT EXISTS signal_trust_weights (
    id SERIAL PRIMARY KEY,
    signal_name TEXT NOT NULL,
    context_bucket TEXT NOT NULL,
    rolling_win_rate NUMERIC NOT NULL,
    n_outcomes_observed INT NOT NULL,
    trust_weight NUMERIC NOT NULL,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (signal_name, context_bucket)
);

CREATE TABLE IF NOT EXISTS signal_trust_history (
    id SERIAL PRIMARY KEY,
    signal_name TEXT NOT NULL,
    context_bucket TEXT NOT NULL,
    trust_weight NUMERIC NOT NULL,
    rolling_win_rate NUMERIC NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _connect():
    url = os.environ.get("AIEM_DATABASE_URL")
    if not url:
        raise RuntimeError("AIEM_DATABASE_URL is not set.")
    return psycopg2.connect(url)


def init_schema():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()


def classify_context_bucket(market_regime_recommendation: Optional[str] = None) -> str:
    """Maps market_regime_overlay's output to a context bucket. Kept
    simple and inspectable on purpose — three buckets, not twenty, so
    each one accumulates enough outcomes to be statistically meaningful."""
    if market_regime_recommendation in ("sit_out", "reduce_exposure"):
        return "volatile_or_cautious_market"
    if market_regime_recommendation == "full_exposure":
        return "calm_supportive_market"
    return "mixed_market"


def update_trust_weight(
    signal_name: str,
    context_bucket: str,
    new_outcome_was_win: bool,
    decay_factor: float = 0.95,
    min_weight: float = 0.2,
    max_weight: float = 2.0,
) -> Dict[str, Any]:
    """Call this every time a new outcome resolves. Updates the
    EXPONENTIALLY-DECAYED rolling win rate for this specific
    signal+context combination, then converts it into a trust weight.

    decay_factor close to 1.0 = slow-moving, long memory. Lower = faster-
    adapting, more reactive to recent results. 0.95 means each new outcome
    carries weight ~5% of the running average vs. 95% history — roughly a
    20-observation effective memory window.

    Trust weight conversion: 1.0 = neutral (50% win rate). Above 1.0 means
    this signal/context combo has been outperforming a coin flip; below 1.0
    means it's been underperforming. Capped at [min_weight, max_weight] so
    no single signal can ever be entirely zeroed out or entirely dominate.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT rolling_win_rate, n_outcomes_observed FROM signal_trust_weights "
                "WHERE signal_name = %s AND context_bucket = %s",
                (signal_name, context_bucket),
            )
            existing = cur.fetchone()

    outcome_value = 1.0 if new_outcome_was_win else 0.0

    if existing:
        prior_rate = float(existing["rolling_win_rate"])
        n_observed = existing["n_outcomes_observed"] + 1
        new_rate = decay_factor * prior_rate + (1 - decay_factor) * outcome_value
    else:
        new_rate = outcome_value
        n_observed = 1

    trust_weight = 1.0 + (new_rate - 0.5) * 2 * (max_weight - 1.0)
    trust_weight = max(min_weight, min(max_weight, trust_weight))

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signal_trust_weights (signal_name, context_bucket, rolling_win_rate, n_outcomes_observed, trust_weight, last_updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (signal_name, context_bucket)
                DO UPDATE SET rolling_win_rate = %s, n_outcomes_observed = %s, trust_weight = %s, last_updated_at = now()
                """,
                (signal_name, context_bucket, new_rate, n_observed, trust_weight,
                 new_rate, n_observed, trust_weight),
            )
            cur.execute(
                """
                INSERT INTO signal_trust_history (signal_name, context_bucket, trust_weight, rolling_win_rate)
                VALUES (%s, %s, %s, %s)
                """,
                (signal_name, context_bucket, trust_weight, new_rate),
            )
        conn.commit()

    return {
        "signal_name": signal_name,
        "context_bucket": context_bucket,
        "rolling_win_rate": round(new_rate, 4),
        "n_outcomes_observed": n_observed,
        "trust_weight": round(trust_weight, 4),
    }


def get_current_trust_weights(context_bucket: Optional[str] = None) -> List[Dict[str, Any]]:
    """The actual lookup function ensemble_combiner.py or
    pre_decision_risk_gate.py should call before weighing a multi-signal
    decision — read these weights and apply them, rather than treating
    every signal as equally trustworthy by default."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if context_bucket:
                cur.execute(
                    "SELECT * FROM signal_trust_weights WHERE context_bucket = %s ORDER BY trust_weight DESC",
                    (context_bucket,),
                )
            else:
                cur.execute("SELECT * FROM signal_trust_weights ORDER BY signal_name, context_bucket")
            return [dict(r) for r in cur.fetchall()]


def get_trust_history(signal_name: str, context_bucket: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Full trust-weight history over time for one signal+context — this
    is what you'd plot to literally SEE a signal's trust rising or falling
    as outcomes accumulate."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT trust_weight, rolling_win_rate, recorded_at FROM signal_trust_history "
                "WHERE signal_name = %s AND context_bucket = %s ORDER BY recorded_at DESC LIMIT %s",
                (signal_name, context_bucket, limit),
            )
            return [dict(r) for r in cur.fetchall()]


def apply_trust_weights_to_candidates(
    candidates: List[Dict[str, Any]],
    context_bucket: str,
    probability_field: str = "probability",
    min_outcomes_to_trust: int = 15,
) -> List[Dict[str, Any]]:
    """Convenience function: takes a list of candidates from ANY scanner
    (each with a 'signal_name' and a probability field) and multiplies
    their probability by the current trust weight for that signal+context.
    Signals with fewer than min_outcomes_to_trust observed outcomes are
    left at neutral weight (1.0) — not enough track record yet to adjust
    trust either up or down."""
    weights = {(w["signal_name"], w["context_bucket"]): w for w in get_current_trust_weights(context_bucket)}

    adjusted = []
    for c in candidates:
        signal_name = c.get("signal_name", "unknown")
        weight_entry = weights.get((signal_name, context_bucket))

        if weight_entry and weight_entry["n_outcomes_observed"] >= min_outcomes_to_trust:
            trust_weight = float(weight_entry["trust_weight"])
        else:
            trust_weight = 1.0

        original_prob = c.get(probability_field, 0)
        adjusted_prob = min(original_prob * trust_weight, 1.0)

        adjusted.append({
            **c,
            "original_probability": original_prob,
            "trust_weight_applied": round(trust_weight, 4),
            "trust_adjusted_probability": round(adjusted_prob, 4),
        })

    adjusted.sort(key=lambda c: c["trust_adjusted_probability"], reverse=True)
    return adjusted


if __name__ == "__main__":
    init_schema()
    print("meta_learning_signal_trust schema ready.")
    print("Call update_trust_weight() every time an outcome resolves.")
    print("Call apply_trust_weights_to_candidates() before finalizing any multi-signal recommendation.")
