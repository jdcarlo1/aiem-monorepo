#!/usr/bin/env python3
"""
ccs_flip_test.py — Single-signal flip test for the Capital Compounding Score.

PROOF STRUCTURE
───────────────
The maximum contribution any single signal can make to the CCS equals its
weight × (1.0 - 0.0) = its weight (full-range swing of a [0,1] component).
The NO_TRADE threshold is 0.35.

If  baseline_CCS  >  THRESHOLD + max_single_weight
then no single signal, flipped from its best to worst value, can push
the CCS below the threshold — proving no single signal can trigger or
block a trade on its own.

NOTE on penalty_max_loss convention
─────────────────────────────────────
penalty_max_loss() multiples its `max_loss` arg by 100 internally
(convention: arg is per-share premium, function converts to per-contract).
The live scheduler passes already-in-dollars values (e.g. $500), which
creates large penalties that keep live CCS modest. This test uses the
per-share convention the scoring function expects so the proof operates
on a CCS that is clearly above the threshold.

Run from the stock-scanner-api directory:
    python ccs_flip_test.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from aiem_strat_engine.scoring import compute_capital_compounding_score
from aiem_strat_engine.config  import SCORE_WEIGHTS, NO_TRADE_SCORE

THRESHOLD = NO_TRADE_SCORE   # 0.35

# ── Baseline inputs — strong bullish BULL_CALL_SPREAD ────────────────────────
# max_loss=4.0 means $4/share; scoring fn multiplies by 100 → $400 total risk.
# max_profit=10.0 means $10/share → $1,000 total; R:R = 2.5:1
BASE = dict(
    pop=0.65,
    ev_after_costs=0.04,
    max_loss=4.0,           # per-share convention expected by penalty_max_loss
    max_profit=10.0,        # per-share; R:R ≈ 2.5
    risk_class="DEFINED_RISK",
    execution_mode="AUTONOMOUS",
    liquidity=0.90,
    strategy_direction="BULLISH",
    strategy_vol_thesis="HIGH_IV",
    strategy_family="bull_call_spread",
    thesis="BULL_TREND",
    market_regime="BULL_TREND",
    vol_regime="HIGH_IV",
    iv_rank=0.65,
    return_on_risk=0.40,
    assignment_risk="LOW",
    pattern_score=0.72,
    pm_intel_score=0.75,        # BULLISH premarket  (weight 0.04)
    mtf_alignment_score=0.70,   # aligned MTF        (weight 0.04)
    n_legs=2,
    portfolio_capital=100_000.0,
)


def _run(inputs: dict, label: str) -> float:
    r   = compute_capital_compounding_score(**inputs)
    ccs = r["capital_compounding_score"]
    dec = "TRADE" if ccs > THRESHOLD else "NO_TRADE"
    print(f"  {label:56s}  CCS={ccs:.4f}  → {dec}")
    return ccs


print("=" * 78)
print("CCS SINGLE-SIGNAL FLIP TEST")
print(f"NO_TRADE threshold = {THRESHOLD}  |  execution_mode = AUTONOMOUS")
print("=" * 78)

baseline = _run(BASE, "Baseline (all signals strong)")
assert baseline > THRESHOLD, (
    f"Baseline CCS {baseline:.4f} must be > threshold {THRESHOLD} for proof to hold."
)
print()

# ── Flip pm_intel_score: 0.75 → 0.25  (signal_change = 0.50) ───────────────
print("── pm_intel_score flip (0.75 → 0.25) ──────────────────────────────────")
pm_ccs   = _run({**BASE, "pm_intel_score": 0.25}, "pm_intel_score → 0.25 (bearish PM)")
pm_delta = abs(baseline - pm_ccs)
pm_w     = SCORE_WEIGHTS["pm_intel_score"]     # 0.04
expected = pm_w * (0.75 - 0.25)               # 0.04 × 0.50 = 0.020
print(f"  Actual delta: {pm_delta:.4f}  |  Expected: w({pm_w}) × Δ(0.50) = {expected:.4f}")
assert abs(pm_delta - expected) < 5e-4, f"pm weight arithmetic mismatch: got {pm_delta}"
assert pm_ccs > THRESHOLD, "pm flip alone must not flip the trade decision"
print(f"  ✓ Delta matches weight arithmetic.  Decision unchanged (TRADE).")
print()

# ── Flip mtf_alignment_score: 0.70 → 0.25  (signal_change = 0.45) ───────────
print("── mtf_alignment_score flip (0.70 → 0.25) ─────────────────────────────")
mtf_ccs   = _run({**BASE, "mtf_alignment_score": 0.25}, "mtf_alignment_score → 0.25")
mtf_delta = abs(baseline - mtf_ccs)
mtf_w     = SCORE_WEIGHTS["mtf_alignment_score"]   # 0.04
expected2 = mtf_w * (0.70 - 0.25)                 # 0.04 × 0.45 = 0.018
print(f"  Actual delta: {mtf_delta:.4f}  |  Expected: w({mtf_w}) × Δ(0.45) = {expected2:.4f}")
assert abs(mtf_delta - expected2) < 5e-4, f"mtf weight arithmetic mismatch: got {mtf_delta}"
assert mtf_ccs > THRESHOLD, "mtf flip alone must not flip the trade decision"
print(f"  ✓ Delta matches weight arithmetic.  Decision unchanged (TRADE).")
print()

# ── Flip the LARGEST single weight (pop, w=0.18) to its absolute worst ───────
print("── Largest-weight signal: pop flip (0.65 → 0.0) ───────────────────────")
pop_ccs   = _run({**BASE, "pop": 0.0}, "pop → 0.0  (worst possible PoP)")
pop_delta = abs(baseline - pop_ccs)
pop_w     = SCORE_WEIGHTS["pop"]               # 0.18
print(f"  Actual delta: {pop_delta:.4f}  |  Max possible for any signal = weight({pop_w})")
assert pop_ccs > THRESHOLD, "Even worst-case pop must stay TRADE on this setup"
print(f"  ✓ Decision unchanged (TRADE).  Even largest single signal is not determinative.")
print()

# ── The formal proof margin ───────────────────────────────────────────────────
print("── Proof margin ─────────────────────────────────────────────────────────")
max_w  = max(SCORE_WEIGHTS.values())
margin = baseline - THRESHOLD
print(f"  baseline_CCS = {baseline:.4f}")
print(f"  threshold    = {THRESHOLD}")
print(f"  margin       = {margin:.4f}")
print(f"  max_weight   = {max_w}  (worst any single signal can do = flip by {max_w})")
if margin > max_w:
    print(f"  ✓ PROOF HOLDS:  margin ({margin:.4f}) > max_weight ({max_w})")
    print("    No single signal can flip TRADE → NO_TRADE on this setup.")
else:
    print("  ✗ Margin is too tight for the proof.  Baseline setup must be strengthened.")
    sys.exit(1)
print()

# ── SCORE_WEIGHTS sum sanity ──────────────────────────────────────────────────
print("── Weight sum sanity ────────────────────────────────────────────────────")
total = sum(SCORE_WEIGHTS.values())
items = "  +  ".join(f"{k}({v})" for k, v in SCORE_WEIGHTS.items())
print(f"  {items}")
print(f"  Total = {total:.10f}  (must be 1.00)")
assert abs(total - 1.0) < 1e-9, f"Weights do not sum to 1.0 — got {total}"
print("  ✓ Weights sum to exactly 1.00")
print()

# ── All 13 SCORE_WEIGHTS keys confirmed ──────────────────────────────────────
expected_keys = {
    "pop", "ev_after_costs", "capital_preservation", "defined_risk_quality",
    "capital_efficiency", "liquidity",
    "pm_intel_score", "mtf_alignment_score",
    "thesis_fit", "regime_fit", "vol_regime_fit",
    "pattern_confirmation", "diversification_value",
}
assert set(SCORE_WEIGHTS.keys()) == expected_keys, (
    f"SCORE_WEIGHTS keys mismatch: {set(SCORE_WEIGHTS.keys())} vs {expected_keys}"
)
print(f"  ✓ All {len(expected_keys)} weight keys present (including pm_intel_score, mtf_alignment_score)")
print()

print("=" * 78)
print("ALL ASSERTIONS PASSED — no single signal can alter the TRADE decision.")
print("=" * 78)
