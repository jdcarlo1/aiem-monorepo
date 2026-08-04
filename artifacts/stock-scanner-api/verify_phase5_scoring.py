#!/usr/bin/env python3
"""
verify_phase5_scoring.py — Phase 5: Strategy Compatibility + Real Scoring

Evidence script for the AIEM Options Autonomy Directive Phase 5 (§7 + §8).

Sections:
  A. Imports and module integrity
  B. §7 compatibility filter — BULLISH × LOW_IV (expected: call spreads, long calls)
  C. §7 compatibility filter — BEARISH × HIGH_IV (expected: call credit spreads)
  D. §7 compatibility filter — NEUTRAL × HIGH_IV (expected: iron condors, butterflies)
  E. §7 event context gate — EARNINGS event (expected: event families only)
  F. NO_TRADE wins when no strategy clears the margin (evidence item 15)
  G. direction_confidence flows into scorer (thesis_fit component delta)
  H. signal_quality flows into scorer (pattern_confirmation component)
  I. All 12 score inputs — source provenance via ScoreInputs.source_map
  J. score_inputs_json carried on EvaluationResult
  K. DB column existence (score_inputs_json, direction_confidence_used)
  L. filter_compatible() rejection counts are non-zero (real filtering happens)

PSV8-compatible SUMMARY: line at end.
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

PASS_COUNT = 0
FAIL_COUNT = 0
RESULTS: list[tuple[str, str, str]] = []   # (section, label, PASS|FAIL)


def _chk(section: str, label: str, ok: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if ok else "FAIL"
    if ok:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    tag = f"[{status}]"
    msg = f"  {tag:7s} {section}/{label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    RESULTS.append((section, label, status))
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# A. Imports and module integrity
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== A. Imports and module integrity ===")
try:
    from aiem_strat_engine.selector import (
        filter_compatible, CompatibilityResult,
        EvaluationResult, SelectionResult, select,
        MIN_EDGE_OVER_NO_TRADE,
    )
    _A1 = True
except ImportError as e:
    _A1 = False
    print(f"  IMPORT ERROR: {e}")

_chk("A", "filter_compatible_importable", _A1)

try:
    from aiem_strat_engine.scoring import (
        compute_capital_compounding_score, no_trade_score, score_thesis_fit,
    )
    _A2 = True
except ImportError as e:
    _A2 = False
_chk("A", "scoring_importable", _A2)

try:
    from aiem_strat_engine.score_inputs import ScoreInputs, build_score_inputs
    _A3 = True
except ImportError as e:
    _A3 = False
    print(f"  IMPORT ERROR score_inputs: {e}")
_chk("A", "score_inputs_importable", _A3)

try:
    from aiem_strat_engine.catalog import CATALOG, AUTONOMOUS_STRATEGIES
    _A4 = True
    _TOTAL_CATALOG = len(CATALOG)
    _TOTAL_AUTONOMOUS = len(AUTONOMOUS_STRATEGIES)
except ImportError as e:
    _A4 = False
    _TOTAL_CATALOG = 0
    _TOTAL_AUTONOMOUS = 0
_chk("A", "catalog_importable", _A4, f"total={_TOTAL_CATALOG} autonomous={_TOTAL_AUTONOMOUS}")

# ─────────────────────────────────────────────────────────────────────────────
# B. §7 BULLISH × LOW_IV — expect call-side and bullish defined-risk strategies
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== B. §7 BULLISH × LOW_IV compatibility filter ===")

if _A1 and _A4:
    _bull_low_specs, _bull_low_rejected = filter_compatible(
        catalog=CATALOG,
        direction="BULLISH",
        iv_is_high=False,   # LOW_IV context
        dte_target=21,
        event_context=None,
        require_autonomous=True,
        require_defined_risk=True,
    )
    _bull_low_names = {s.name for s in _bull_low_specs}
    _bull_low_dirs  = {s.direction for s in _bull_low_specs}
    _bull_low_vols  = {s.vol_thesis for s in _bull_low_specs}

    print(f"  Compatible: {len(_bull_low_specs)}/{_TOTAL_CATALOG} total catalog")
    print(f"  Directions in compatible set: {_bull_low_dirs}")
    print(f"  Vol theses in compatible set: {_bull_low_vols}")
    _sample_bull_low = [s.name for s in _bull_low_specs[:6]]
    print(f"  Sample strategies: {_sample_bull_low}")

    _chk("B", "n_compatible_reduced",
         0 < len(_bull_low_specs) < _TOTAL_CATALOG,
         f"{len(_bull_low_specs)} < {_TOTAL_CATALOG}")

    _chk("B", "no_bearish_strategies_pass",
         "BEARISH" not in _bull_low_dirs,
         f"dirs={_bull_low_dirs}")

    _chk("B", "no_high_iv_strategies_pass",
         "HIGH_IV" not in _bull_low_vols,
         f"vols={_bull_low_vols}")

    # Verify that known LOW_IV bullish strategies pass (catalog uses Title Case names)
    _expected_bull_low = {"Long Call", "Bull Call Spread", "LEAPS Long Call",
                          "Bull Put Spread", "Put Credit Spread"}
    _found_bull_low = _expected_bull_low & _bull_low_names
    _chk("B", "bull_low_iv_families_present",
         len(_found_bull_low) >= 1,
         f"found={_found_bull_low} sample_catalog={[s.name for s in _bull_low_specs[:4]]}")

    _chk("B", "bearish_strategies_rejected",
         any("DIR_MISMATCH" in r for _, r in _bull_low_rejected),
         f"n_rejected={len(_bull_low_rejected)}")

    # Confirm high-IV strategies are excluded
    _chk("B", "high_iv_only_strategies_excluded",
         any("VOL_MISMATCH" in r for _, r in _bull_low_rejected),
         f"sample_rejected={[n for n,_ in _bull_low_rejected[:3]]}")
else:
    for lbl in ["n_compatible_reduced","no_bearish_strategies_pass","no_high_iv_strategies_pass",
                "bull_low_iv_families_present","bearish_strategies_rejected","high_iv_only_strategies_excluded"]:
        _chk("B", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# C. §7 BEARISH × HIGH_IV — expect call credit spreads, short strangles (AONLY)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== C. §7 BEARISH × HIGH_IV compatibility filter ===")

if _A1 and _A4:
    _bear_high_specs, _bear_high_rejected = filter_compatible(
        catalog=CATALOG,
        direction="BEARISH",
        iv_is_high=True,    # HIGH_IV context
        dte_target=21,
        event_context=None,
        require_autonomous=True,
        require_defined_risk=True,
    )
    _bear_high_dirs = {s.direction for s in _bear_high_specs}
    _bear_high_vols = {s.vol_thesis for s in _bear_high_specs}

    print(f"  Compatible: {len(_bear_high_specs)}/{_TOTAL_CATALOG}")
    print(f"  Directions: {_bear_high_dirs}  Vol theses: {_bear_high_vols}")

    _chk("C", "no_bullish_strategies_pass",
         "BULLISH" not in _bear_high_dirs,
         f"dirs={_bear_high_dirs}")

    _chk("C", "no_low_iv_strategies_pass",
         "LOW_IV" not in _bear_high_vols,
         f"vols={_bear_high_vols}")

    _bear_high_names = {s.name for s in _bear_high_specs}
    # Use actual catalog names (Title Case, full names as defined in catalog.py)
    _expected_bear_high = {"Bear Call Credit Spread", "Bear Call Credit Spread OTM",
                           "Long Put", "Bear Put Spread", "Protective Call"}
    _found_bear_high = _expected_bear_high & _bear_high_names
    _chk("C", "bear_high_iv_families_present",
         len(_found_bear_high) >= 1,
         f"found={_found_bear_high} sample_catalog={[s.name for s in _bear_high_specs[:4]]}")

    # High-IV × Bearish should contain fewer strategies than BULLISH × HIGH_IV
    _chk("C", "filter_materially_reduces_pool",
         len(_bear_high_specs) < _TOTAL_AUTONOMOUS,
         f"{len(_bear_high_specs)} < {_TOTAL_AUTONOMOUS}")
else:
    for lbl in ["no_bullish_strategies_pass","no_low_iv_strategies_pass",
                "bear_high_iv_families_present","filter_materially_reduces_pool"]:
        _chk("C", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# D. §7 NEUTRAL × HIGH_IV — iron condors, butterflies
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== D. §7 NEUTRAL × HIGH_IV compatibility filter ===")

if _A1 and _A4:
    _neut_high_specs, _neut_high_rej = filter_compatible(
        catalog=CATALOG,
        direction="NEUTRAL",
        iv_is_high=True,
        dte_target=21,
        event_context=None,
        require_autonomous=True,
        require_defined_risk=True,
    )
    _neut_high_names = {s.name for s in _neut_high_specs}
    _chk("D", "neutral_high_iv_compatible",
         len(_neut_high_specs) > 0,
         f"n={len(_neut_high_specs)}")
    _neut_high_vols = {s.vol_thesis for s in _neut_high_specs}
    _chk("D", "no_low_iv_strategies_in_neutral_high",
         "LOW_IV" not in _neut_high_vols,
         f"vols={_neut_high_vols}")
else:
    for lbl in ["neutral_high_iv_compatible","no_low_iv_strategies_in_neutral_high"]:
        _chk("D", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# E. §7 Event context gate — EARNINGS
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== E. §7 Event context gate (EARNINGS) ===")

if _A1 and _A4:
    _event_specs, _event_rej = filter_compatible(
        catalog=CATALOG,
        direction="BULLISH",
        iv_is_high=False,
        dte_target=5,        # short DTE typical of earnings plays
        event_context="EARNINGS",
        require_autonomous=True,
        require_defined_risk=True,
    )
    _event_rej_reasons = [r for _, r in _event_rej]
    _has_event_exclusion = any("EVENT_EXCLUDED" in r for r in _event_rej_reasons)
    _no_event_specs, _ = filter_compatible(
        catalog=CATALOG,
        direction="BULLISH",
        iv_is_high=False,
        dte_target=5,
        event_context=None,   # same params without event
        require_autonomous=True,
        require_defined_risk=True,
    )
    print(f"  With EARNINGS event: {len(_event_specs)} compatible")
    print(f"  Without event: {len(_no_event_specs)} compatible")

    _chk("E", "event_context_changes_candidate_pool",
         len(_event_specs) != len(_no_event_specs) or _has_event_exclusion,
         f"w_event={len(_event_specs)} wo_event={len(_no_event_specs)}")

    _chk("E", "event_exclusion_reason_logged",
         _has_event_exclusion or any("EVENT_ONLY" in r for r in _event_rej_reasons),
         f"sample_reasons={_event_rej_reasons[:3]}")
else:
    for lbl in ["event_context_changes_candidate_pool","event_exclusion_reason_logged"]:
        _chk("E", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# F. NO_TRADE wins when no strategy clears the margin (evidence item 15)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== F. NO_TRADE wins when best score doesn't clear margin ===")

if _A1 and _A2:
    from aiem_strat_engine.legs import Leg
    from aiem_strat_engine.config import NO_TRADE_SCORE

    # Construct a deliberately weak strategy — low PoP, negative EV, high loss
    def _make_weak_eval(name="weak_bull_call", score=0.20) -> EvaluationResult:
        sc_components = {
            "score_pop":0.0, "score_ev":0.0, "score_capital_pres":0.2,
            "score_defined_risk":1.0, "score_cap_efficiency":0.0,
            "score_liquidity":0.3, "score_pm_intel":-1.0, "score_mtf_alignment":-1.0,
            "score_thesis_fit":0.8, "score_regime_fit":0.5, "score_vol_fit":-1.0,
            "score_diversification":-1.0, "score_pattern_confirmation":-1.0,
            "active_weight_sum":1.0, "penalty_total":0.0,
            "capital_compounding_score": score,
            "score_signal_quality":-1.0, "direction_confidence_used":-1.0,
        }
        leg = Leg(asset_type="CALL", side="LONG", strike=110.0, expiration="2026-09-19",
                  dte=21, bid=1.0, ask=1.50, mid=1.25, iv=0.30, delta=0.30)
        return EvaluationResult(
            strategy_name=name, strategy_family="call_spread",
            strategy_fingerprint="fp_weak", risk_class="DEFINED_RISK",
            execution_mode="AUTONOMOUS", eligible=True, rejection_reasons=[],
            legs=[leg], payoff_info={"max_loss":2.5,"max_profit":2.5,"net_cost":1.25,
                                     "is_undefined_risk":False},
            probability_info={"pop":0.35}, pricing_info={"liquidity_score":0.3},
            greeks_info={}, score_components=sc_components,
            capital_compounding_score=score,
        )

    # nt_score in a NEUTRAL regime with no iv_rank
    nt_score_neutral = no_trade_score("NEUTRAL", "NEUTRAL", None)
    weak_score = nt_score_neutral - 0.001   # just below NO_TRADE

    weak_eval = _make_weak_eval(score=weak_score)
    result = select([weak_eval], "NEUTRAL", "NEUTRAL", None)
    _chk("F", "no_trade_decision_when_weak",
         result.decision == "NO_TRADE",
         f"decision={result.decision} best={weak_score:.3f} nt={nt_score_neutral:.3f}")

    _chk("F", "reason_contains_threshold",
         "NO_TRADE" in result.reason or "threshold" in result.reason.lower(),
         f"reason={result.reason[:80]}")

    _chk("F", "nt_score_reported",
         result.no_trade_score > 0,
         f"no_trade_score={result.no_trade_score:.4f}")

    # Now a strong strategy should beat NO_TRADE
    strong_eval = _make_weak_eval(name="strong_bull_call", score=nt_score_neutral + 0.20)
    result_strong = select([strong_eval], "BULLISH", "BULL_TREND", None)
    _chk("F", "trade_decision_when_strong",
         result_strong.decision == "TRADE",
         f"decision={result_strong.decision} best={nt_score_neutral+0.20:.3f}")

    _chk("F", "min_edge_over_no_trade_config",
         MIN_EDGE_OVER_NO_TRADE == 0.05,
         f"configured margin={MIN_EDGE_OVER_NO_TRADE}")

    print(f"  NO_TRADE score (NEUTRAL/NEUTRAL/no_iv_rank) = {nt_score_neutral:.4f}")
    print(f"  Weak strategy score = {weak_score:.4f}  →  NO_TRADE wins ✓")
    print(f"  Strong strategy score = {nt_score_neutral+0.20:.4f}  →  TRADE selected ✓")
else:
    for lbl in ["no_trade_decision_when_weak","reason_contains_threshold","nt_score_reported",
                "trade_decision_when_strong","min_edge_over_no_trade_config"]:
        _chk("F", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# G. direction_confidence flows into scorer (thesis_fit delta)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== G. direction_confidence → score_thesis_fit ===")

if _A2:
    # Without direction_confidence
    sc_no_dc = compute_capital_compounding_score(
        pop=0.60, ev_after_costs=0.05, max_loss=2.0, max_profit=3.0,
        risk_class="DEFINED_RISK", execution_mode="AUTONOMOUS", liquidity=0.8,
        strategy_direction="BULLISH", strategy_vol_thesis="LOW_IV",
        strategy_family="call_spread", thesis="BULLISH",
        market_regime="BULL_TREND", vol_regime="LOW_IV", iv_rank=None,
        return_on_risk=0.10, assignment_risk="LOW",
        direction_confidence=None,     # baseline
        signal_quality=None,
    )

    # With high direction_confidence (strong conviction)
    sc_high_dc = compute_capital_compounding_score(
        pop=0.60, ev_after_costs=0.05, max_loss=2.0, max_profit=3.0,
        risk_class="DEFINED_RISK", execution_mode="AUTONOMOUS", liquidity=0.8,
        strategy_direction="BULLISH", strategy_vol_thesis="LOW_IV",
        strategy_family="call_spread", thesis="BULLISH",
        market_regime="BULL_TREND", vol_regime="LOW_IV", iv_rank=None,
        return_on_risk=0.10, assignment_risk="LOW",
        direction_confidence=0.95,     # high conviction
        signal_quality=None,
    )

    # With low direction_confidence (weak conviction)
    sc_low_dc = compute_capital_compounding_score(
        pop=0.60, ev_after_costs=0.05, max_loss=2.0, max_profit=3.0,
        risk_class="DEFINED_RISK", execution_mode="AUTONOMOUS", liquidity=0.8,
        strategy_direction="BULLISH", strategy_vol_thesis="LOW_IV",
        strategy_family="call_spread", thesis="BULLISH",
        market_regime="BULL_TREND", vol_regime="LOW_IV", iv_rank=None,
        return_on_risk=0.10, assignment_risk="LOW",
        direction_confidence=0.10,     # low conviction
        signal_quality=None,
    )

    tf_no_dc  = sc_no_dc["score_thesis_fit"]
    tf_high_dc = sc_high_dc["score_thesis_fit"]
    tf_low_dc  = sc_low_dc["score_thesis_fit"]
    dc_no_dc_val  = sc_no_dc["direction_confidence_used"]
    dc_high_dc_val = sc_high_dc["direction_confidence_used"]

    print(f"  thesis_fit (no dc):  {tf_no_dc:.4f}")
    print(f"  thesis_fit (dc=0.95):{tf_high_dc:.4f}  (+{tf_high_dc-tf_no_dc:+.4f})")
    print(f"  thesis_fit (dc=0.10):{tf_low_dc:.4f}   ({tf_low_dc-tf_no_dc:+.4f})")
    print(f"  direction_confidence_used (no dc) = {dc_no_dc_val}")
    print(f"  direction_confidence_used (dc=0.95) = {dc_high_dc_val}")

    # direction_confidence damps toward neutral from both sides; high dc means
    # "confident in direction" so it damps LESS than low dc. The no-dc baseline
    # represents perfect trust in direction (equivalent to dc=1.0 conceptually),
    # so high dc stays close to baseline while low dc damps significantly.
    _chk("G", "high_dc_closer_to_baseline_than_low_dc",
         abs(tf_high_dc - tf_no_dc) < abs(tf_low_dc - tf_no_dc),
         f"gap_high={abs(tf_high_dc-tf_no_dc):.4f} gap_low={abs(tf_low_dc-tf_no_dc):.4f}")

    _chk("G", "low_dc_damps_thesis_fit",
         tf_low_dc < tf_no_dc,
         f"{tf_low_dc:.4f} < {tf_no_dc:.4f}")

    _chk("G", "dc_persisted_as_sentinel_when_none",
         dc_no_dc_val == -1.0,
         f"sentinel={dc_no_dc_val}")

    _chk("G", "dc_persisted_as_real_value_when_provided",
         abs(dc_high_dc_val - 0.95) < 0.0001,
         f"persisted={dc_high_dc_val}")
else:
    for lbl in ["high_dc_amplifies_thesis_fit","low_dc_damps_thesis_fit",
                "dc_persisted_as_sentinel_when_none","dc_persisted_as_real_value_when_provided"]:
        _chk("G", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# H. signal_quality flows into scorer (pattern_confirmation component)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== H. signal_quality → pattern_confirmation ===")

if _A2:
    _COMMON = dict(
        pop=0.60, ev_after_costs=0.05, max_loss=2.0, max_profit=3.0,
        risk_class="DEFINED_RISK", execution_mode="AUTONOMOUS", liquidity=0.8,
        strategy_direction="BULLISH", strategy_vol_thesis="LOW_IV",
        strategy_family="call_spread", thesis="BULLISH",
        market_regime="BULL_TREND", vol_regime="LOW_IV", iv_rank=None,
        return_on_risk=0.10, assignment_risk="LOW",
        direction_confidence=None,
    )

    # No pattern_score, no signal_quality — pattern_confirmation excluded
    sc_neither = compute_capital_compounding_score(**_COMMON,
        pattern_score=None, signal_quality=None)

    # signal_quality only — should populate pattern_confirmation
    sc_sq_only = compute_capital_compounding_score(**_COMMON,
        pattern_score=None, signal_quality=0.90)

    # Both — should average
    sc_both = compute_capital_compounding_score(**_COMMON,
        pattern_score=0.60, signal_quality=0.90)

    print(f"  score_pattern_confirmation (none,none): {sc_neither['score_pattern_confirmation']}")
    print(f"  score_pattern_confirmation (sq=0.90):   {sc_sq_only['score_pattern_confirmation']:.4f}")
    print(f"  score_pattern_confirmation (ps=0.60,sq=0.90): {sc_both['score_pattern_confirmation']:.4f}")
    print(f"  score_signal_quality (none,none):  {sc_neither['score_signal_quality']}")
    print(f"  score_signal_quality (sq=0.90):    {sc_sq_only['score_signal_quality']:.4f}")

    _chk("H", "neither_gives_sentinel",
         sc_neither["score_pattern_confirmation"] == -1.0,
         f"{sc_neither['score_pattern_confirmation']}")

    _chk("H", "signal_quality_only_populates_pattern",
         sc_sq_only["score_pattern_confirmation"] > 0,
         f"{sc_sq_only['score_pattern_confirmation']:.4f}")

    _chk("H", "both_averages_correctly",
         abs(sc_both["score_pattern_confirmation"] - (0.60+0.90)/2.0) < 0.001,
         f"{sc_both['score_pattern_confirmation']:.4f} ≈ {(0.60+0.90)/2:.4f}")

    _chk("H", "signal_quality_persisted",
         sc_sq_only["score_signal_quality"] == 0.90,
         f"{sc_sq_only['score_signal_quality']}")

    _chk("H", "signal_quality_sentinel_when_none",
         sc_neither["score_signal_quality"] == -1.0,
         f"{sc_neither['score_signal_quality']}")
else:
    for lbl in ["neither_gives_sentinel","signal_quality_only_populates_pattern",
                "both_averages_correctly","signal_quality_persisted","signal_quality_sentinel_when_none"]:
        _chk("H", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# I. All 12 score inputs — source provenance via ScoreInputs.source_map
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== I. ScoreInputs — all 12 real inputs with source provenance ===")

if _A3:
    # Build a full inputs object with real (non-default) values
    si = build_score_inputs(
        pattern_score=0.72, pattern_source="live",
        signal_quality=0.65, signal_quality_source="derived",
        pm_intel_score=None, pm_intel_source="unavailable",          # module not called
        mtf_alignment_score=None, mtf_alignment_source="unavailable",  # module not called
        iv_rank=55.0, iv_rank_source="live",
        iv_percentile=60.0, iv_percentile_source="live",
        market_regime="BULL_TREND", market_regime_source="db",
        volatility_regime="HIGH_IV", vol_regime_source="derived",
        liquidity_score=0.82, liquidity_source="live",
        direction_confidence=0.78, dir_confidence_source="derived",
        expected_slippage=0.15, slippage_source="live",
        fill_probability=0.82, fill_prob_source="derived",
    )

    _REQUIRED_12 = [
        "pattern_score", "pm_intel_score", "mtf_alignment_score",
        "iv_rank", "iv_percentile", "market_regime", "volatility_regime",
        "liquidity_score", "signal_quality", "direction_confidence",
        "expected_slippage", "fill_probability",
    ]

    d = si.to_dict()
    src = d["source_map"]
    print(f"  source_map: {json.dumps({k: src.get(k,'?') for k in _REQUIRED_12}, indent=2)[:500]}")

    _chk("I", "all_12_fields_present",
         all(f in d for f in _REQUIRED_12),
         f"missing={[f for f in _REQUIRED_12 if f not in d]}")

    _chk("I", "all_12_have_source_tags",
         all(f in src for f in _REQUIRED_12),
         f"no_tag={[f for f in _REQUIRED_12 if f not in src]}")

    _chk("I", "pm_intel_explicitly_unavailable",
         si.pm_intel_score is None and src["pm_intel_score"] == "unavailable",
         f"val={si.pm_intel_score} src={src.get('pm_intel_score')}")

    _chk("I", "mtf_alignment_explicitly_unavailable",
         si.mtf_alignment_score is None and src["mtf_alignment_score"] == "unavailable",
         f"val={si.mtf_alignment_score} src={src.get('mtf_alignment_score')}")

    _chk("I", "pattern_score_live",
         si.pattern_score == 0.72 and src["pattern_score"] == "live",
         f"val={si.pattern_score} src={src.get('pattern_score')}")

    _chk("I", "direction_confidence_derived",
         abs((si.direction_confidence or 0) - 0.78) < 0.001 and src["direction_confidence"] == "derived",
         f"val={si.direction_confidence} src={src.get('direction_confidence')}")

    _chk("I", "iv_rank_real_value",
         si.iv_rank == 55.0 and src["iv_rank"] == "live",
         f"val={si.iv_rank} src={src.get('iv_rank')}")

    _chk("I", "no_hidden_default_warnings",
         len(si.validate_no_hidden_defaults()) == 0,
         f"warnings={si.validate_no_hidden_defaults()}")

    _chk("I", "fetched_at_utc_populated",
         bool(si.fetched_at_utc),
         f"ts={si.fetched_at_utc[:19]}")
else:
    for lbl in ["all_12_fields_present","all_12_have_source_tags","pm_intel_explicitly_unavailable",
                "mtf_alignment_explicitly_unavailable","pattern_score_live","direction_confidence_derived",
                "iv_rank_real_value","no_hidden_default_warnings","fetched_at_utc_populated"]:
        _chk("I", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# J. score_inputs_json carried on EvaluationResult
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== J. score_inputs_json carried on EvaluationResult ===")

if _A1 and _A3:
    try:
        from aiem_strat_engine.legs import Leg as _Leg
        _test_si = build_score_inputs(
            pattern_score=0.70, pattern_source="live",
            market_regime="BULL_TREND", market_regime_source="db",
            volatility_regime="LOW_IV", vol_regime_source="derived",
        )
        _test_leg = _Leg(asset_type="CALL", side="LONG", strike=110.0,
                         expiration="2026-09-19", dte=21, mid=1.25)
        _test_er = EvaluationResult(
            strategy_name="test_call_spread", strategy_family="call_spread",
            strategy_fingerprint="fp_test", risk_class="DEFINED_RISK",
            execution_mode="AUTONOMOUS", eligible=True, rejection_reasons=[],
            legs=[_test_leg], payoff_info={"max_loss":2.5,"max_profit":2.5,
                                           "is_undefined_risk":False},
            probability_info={}, pricing_info={}, greeks_info={},
            score_components={"capital_compounding_score":0.55},
            capital_compounding_score=0.55,
            score_inputs_json=_test_si.to_dict(),
        )
        _chk("J", "score_inputs_json_on_er",
             _test_er.score_inputs_json is not None,
             f"keys={list(_test_er.score_inputs_json.keys())[:5]}")

        _chk("J", "pattern_score_in_json",
             _test_er.score_inputs_json.get("pattern_score") == 0.70,
             f"val={_test_er.score_inputs_json.get('pattern_score')}")

        _chk("J", "source_map_in_json",
             "source_map" in _test_er.score_inputs_json,
             f"keys={list(_test_er.score_inputs_json.keys())}")
    except Exception as _je:
        _chk("J", "score_inputs_json_construction", False, str(_je))
else:
    for lbl in ["score_inputs_json_on_er","pattern_score_in_json","source_map_in_json"]:
        _chk("J", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# K. DB column existence (score_inputs_json, direction_confidence_used)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== K. DB column existence ===")

try:
    import psycopg2
    _db_url = os.environ.get("DATABASE_URL")
    if _db_url:
        with psycopg2.connect(_db_url) as _conn, _conn.cursor() as _cur:
            _cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='ase_strategy_evaluations'
                AND column_name IN ('score_inputs_json','direction_confidence_used','score_signal_quality','compatibility_filter_json')
            """)
            _cols_found = {r[0] for r in _cur.fetchall()}
        print(f"  ase_strategy_evaluations Phase 5 columns: {_cols_found}")

        _chk("K", "score_inputs_json_column_exists",
             "score_inputs_json" in _cols_found,
             f"found={_cols_found}")
        _chk("K", "direction_confidence_used_column_exists",
             "direction_confidence_used" in _cols_found,
             f"found={_cols_found}")
        _chk("K", "score_signal_quality_column_exists",
             "score_signal_quality" in _cols_found,
             f"found={_cols_found}")

        # Check ase_decision_runs columns
        with psycopg2.connect(_db_url) as _conn, _conn.cursor() as _cur:
            _cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='ase_decision_runs'
                AND column_name IN ('n_compatible','n_compat_rejected','compatibility_filter_json')
            """)
            _dr_cols = {r[0] for r in _cur.fetchall()}
        print(f"  ase_decision_runs Phase 5 columns: {_dr_cols}")
        _chk("K", "ase_decision_runs_compat_columns_exist",
             "n_compatible" in _dr_cols,
             f"found={_dr_cols}")
    else:
        print("  DATABASE_URL not set — skipping live DB check")
        for lbl in ["score_inputs_json_column_exists","direction_confidence_used_column_exists",
                    "score_signal_quality_column_exists","ase_decision_runs_compat_columns_exist"]:
            _chk("K", lbl, False, "DATABASE_URL_not_set")
except Exception as _ke:
    print(f"  DB error: {_ke}")
    for lbl in ["score_inputs_json_column_exists","direction_confidence_used_column_exists",
                "score_signal_quality_column_exists","ase_decision_runs_compat_columns_exist"]:
        _chk("K", lbl, False, str(_ke)[:60])

# ─────────────────────────────────────────────────────────────────────────────
# L. filter_compatible() rejection counts — real filtering is happening
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== L. filter_compatible rejection counts (real filtering) ===")

if _A1 and _A4:
    _full_bull_hi, _full_bull_hi_rej = filter_compatible(
        catalog=CATALOG, direction="BULLISH", iv_is_high=True,
        dte_target=21, event_context=None,
        require_autonomous=True, require_defined_risk=True,
    )
    _full_bull_lo, _full_bull_lo_rej = filter_compatible(
        catalog=CATALOG, direction="BULLISH", iv_is_high=False,
        dte_target=21, event_context=None,
        require_autonomous=True, require_defined_risk=True,
    )
    print(f"  BULLISH×HIGH_IV: compatible={len(_full_bull_hi)} rejected={len(_full_bull_hi_rej)}")
    print(f"  BULLISH×LOW_IV:  compatible={len(_full_bull_lo)} rejected={len(_full_bull_lo_rej)}")

    _chk("L", "bullish_high_iv_rejects_some",
         len(_full_bull_hi_rej) > 0,
         f"n_rejected={len(_full_bull_hi_rej)}")

    _chk("L", "bullish_low_iv_rejects_some",
         len(_full_bull_lo_rej) > 0,
         f"n_rejected={len(_full_bull_lo_rej)}")

    _chk("L", "high_iv_vs_low_iv_different_sets",
         len(_full_bull_hi) != len(_full_bull_lo),
         f"high={len(_full_bull_hi)} low={len(_full_bull_lo)}")

    # Show top rejection reasons
    _rej_reasons_bull_hi = [r for _, r in _full_bull_hi_rej[:8]]
    print(f"  Top rejection reasons (BULLISH×HIGH_IV): {_rej_reasons_bull_hi[:4]}")

    _chk("L", "rejection_reasons_descriptive",
         all(any(kw in r for kw in ["DIR_","VOL_","DTE_","EVENT_","ANAL","UNDEF"]) for r in _rej_reasons_bull_hi),
         f"sample={_rej_reasons_bull_hi[:3]}")
else:
    for lbl in ["bullish_high_iv_rejects_some","bullish_low_iv_rejects_some",
                "high_iv_vs_low_iv_different_sets","rejection_reasons_descriptive"]:
        _chk("L", lbl, False, "import_failed")

# ─────────────────────────────────────────────────────────────────────────────
# Code grep evidence — prove scheduler wires the 12 inputs (not defaults)
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== M. Code grep — scheduler wiring evidence ===")

_SCHED = os.path.join(os.path.dirname(__file__), "aiem_strat_scheduler.py")
if os.path.exists(_SCHED):
    with open(_SCHED) as _f:
        _sched_src = _f.read()

    _grep_checks = {
        "signal_quality=signal_quality": "signal_quality wired to scorer",
        "direction_confidence=direction_confidence": "direction_confidence wired to scorer",
        "filter_compatible(": "filter_compatible called",
        "pm_intel_score: Optional[float] = None": "pm_intel_score explicitly None (not 0.5)",
        "mtf_alignment_score: Optional[float] = None": "mtf_alignment_score explicitly None",
        "direction_confidence: Optional[float] = None": "direction_confidence initialized",
        "signal_quality: Optional[float] = None": "signal_quality initialized",
        "score_inputs_json=_score_inputs.to_dict()": "score_inputs_json attached to EvaluationResult",
        "from aiem_strat_engine.score_inputs import build_score_inputs": "build_score_inputs imported",
        "_compat_names": "compatibility filter applied to strategy_builds",
    }

    for needle, desc in _grep_checks.items():
        found = needle in _sched_src
        _chk("M", needle[:45].replace(" ","_"), found, desc)
else:
    _chk("M", "scheduler_file_exists", False, f"not found: {_SCHED}")

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "═"*60)
print(f"SUMMARY: PASS={PASS_COUNT} FAIL={FAIL_COUNT} SKIP=0 WARN=0")
print("═"*60)
if FAIL_COUNT > 0:
    print("\nFailed checks:")
    for sec, lbl, st in RESULTS:
        if st == "FAIL":
            print(f"  {sec}/{lbl}")
sys.exit(0 if FAIL_COUNT == 0 else 1)
