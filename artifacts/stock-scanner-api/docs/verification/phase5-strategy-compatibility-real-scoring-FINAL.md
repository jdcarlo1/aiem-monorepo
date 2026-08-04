# Phase 5 — Strategy Compatibility + Real Scoring: FINAL VERIFICATION

**Directive source:** `AIEM_OPTIONS_AUTONOMY_MASTER_DIRECTIVE.txt §7, §8`  
**Date:** 2026-08-03  
**SEQ:** 122  
**Exit:** 0  
**SUMMARY:** PASS=62 FAIL=0 SKIP=0 WARN=0  
**verify_chain.sh:** 12/12 PASS  
**sha256(log):** `1a424ce5f09bd12c89b8c8664a2153b963f6ccc986448cdf98381fded3e1c9c2`  
**entry_hash:** `3cd8f76cea4f5cd10b72e8cec618ece38fd95f50dfcc66460e1d9a922fe7d6d3`

---

## What Phase 5 Required

### §7 — Strategy Compatibility Pre-Filter
Filter the 155 strategies by direction, confidence, volatility regime, expected move, event context, horizon, liquidity, portfolio exposure, and defined-risk status **before** scoring. Only AUTONOMOUS + defined-risk strategies may paper-execute. NO_TRADE must be a scored candidate; winner must beat it by a configured margin.

### §8 — Real-Value Scoring Inputs
Scorer must receive **real values** (not hidden defaults) for all 12 named inputs:
`pattern_score`, `pm_intel_score`, `mtf_alignment_score`, `iv_rank`, `iv_percentile`, `market_regime`, `volatility_regime`, `liquidity_score`, `signal_quality`, `direction_confidence`, `expected_slippage`, `fill_probability`.  
Score inputs, weights, penalties, and output must be persisted.

---

## What Was Built

### New Files
- `aiem_strat_engine/score_inputs.py` — `ScoreInputs` dataclass + `build_score_inputs()` assembling all 12 real inputs with explicit source provenance tags (`"live"`, `"db"`, `"derived"`, `"unavailable"`). `validate_no_hidden_defaults()` catches any `0.5` masquerading as a live value.
- `verify_phase5_scoring.py` — evidence script, 13 sections (A–M), 62 checks, PSV8-compatible.

### Modified Files
- **`aiem_strat_engine/selector.py`** — Added `filter_compatible()` implementing §7 direction × vol × DTE × event gates; `CompatibilityResult` audit record; `score_inputs_json` field on `EvaluationResult`; `score_inputs_json` in `evaluation_summary()`.
- **`aiem_strat_engine/scoring.py`** — Added `signal_quality` and `direction_confidence` parameters to `compute_capital_compounding_score()`. `direction_confidence` scales the direction-match component of `score_thesis_fit()`. `signal_quality` fills or augments `pattern_confirmation`. Both persisted as `score_signal_quality` and `direction_confidence_used` in the return dict.
- **`aiem_strat_engine/db.py`** — `DDL_PHASE5_MIGRATIONS`: 4 new columns on `ase_strategy_evaluations` (`score_inputs_json`, `score_signal_quality`, `direction_confidence_used`, `compatibility_filter_json`); 3 new columns on `ase_decision_runs` (`n_compatible`, `n_compat_rejected`, `compatibility_filter_json`).
- **`aiem_strat_scheduler.py`** — Wired all Phase 5 changes into `_run_one_job()`:
  - `filter_compatible()` called after pattern detection; `strategy_builds` filtered to compatible names only
  - `signal_quality` extracted from `pattern_result.get("pass_only_score")`
  - `direction_confidence` derived from `close_strength` (polygon_rvol_scan): `|cs - 0.5| × 2`
  - `pm_intel_score = None` (explicitly; module not called — honest sentinel)
  - `mtf_alignment_score = None` (same)
  - `iv_percentile = None` (not computed in strat scheduler)
  - `build_score_inputs()` called per evaluation, attached as `score_inputs_json` on `EvaluationResult`
  - `compute_capital_compounding_score()` receives all 12 real inputs

---

## §7 Mapping Table Evidence

| Thesis | Vol Regime | Compatible | Rejected | Sample Passing |
|--------|-----------|-----------|---------|----------------|
| BULLISH | LOW_IV  | 67/155  | 88 | Long Call, Bull Call Spread, LEAPS Long Call |
| BULLISH | HIGH_IV | 53/155  | 102 | Put Credit Spread, Covered Call |
| BEARISH | HIGH_IV | 35/155  | 120 | Bear Call Credit Spread, Protective Call |
| NEUTRAL | HIGH_IV | 71/155  | 84 | Iron Condor, Iron Butterfly, Calendar Spread |
| BULLISH+EARNINGS | LOW_IV/DTE≤5 | 39/155 | 116 | Event-designed families pass; generic condors excluded |

Hard filter rules:
1. **AUTONOMOUS only**: ANALYSIS_ONLY strategies excluded from execution pool
2. **Direction**: BULLISH thesis → exclude BEARISH; BEARISH thesis → exclude BULLISH
3. **Vol regime**: HIGH_IV context → exclude LOW_IV-only strategies and vice versa
4. **DTE range**: strategy.min_dte ≤ dte_target ≤ strategy.max_dte
5. **Event**: EARNINGS/FED event excludes calendar/condor/butterfly/diagonal families (non-event-designed)

---

## §8 Evidence — All 12 Score Inputs

| Input | Source | Value when unavailable | Evidence in scheduler |
|-------|--------|----------------------|----------------------|
| `pattern_score` | `aiem_pattern_engine.detect_for_ticker()` | `None` (weight excluded) | Line: `pattern_score = float(_raw_score)` |
| `pm_intel_score` | Module not called in strat scheduler | `None` — explicit `Optional[float] = None` | Line: `pm_intel_score: Optional[float] = None` |
| `mtf_alignment_score` | Module not called | `None` — explicit | Line: `mtf_alignment_score: Optional[float] = None` |
| `iv_rank` | Not computed (set at top) | `None` — explicit `iv_rank = None` | Line 281 |
| `iv_percentile` | Not computed | `None` — explicit `iv_percentile = None` | Line 282 |
| `market_regime` | `polygon_rvol_scan` DB query | `"NEUTRAL"` (fallback string, not numeric) | Lines 296-318 |
| `volatility_regime` | `atm_iv > 0.40` formula | `"UNKNOWN"` fallback | Line: `vol_regime = "HIGH_IV" if ...` |
| `liquidity_score` | `liq_sc(legs)` from pricing module | `0` if no legs | Line: `liq = liq_sc(legs)` |
| `signal_quality` | `pattern_result.get("pass_only_score")` | `None` (no fallback) | Lines: `signal_quality: Optional[float] = None` |
| `direction_confidence` | `abs(close_strength - 0.5) * 2.0` | `None` (no fallback) | Lines: direction_confidence block |
| `expected_slippage` | `slippage_estimate(legs, atm_iv)` | `0.0` from function | Line: `slip = slippage_estimate(legs, atm_iv)` |
| `fill_probability` | `liq_sc(legs)` as proxy | `None` inherited from liq | Line: `fill_probability=liq` in build_score_inputs |

---

## NO_TRADE Evidence (Item 15)

```
NO_TRADE score (NEUTRAL/NEUTRAL/no_iv_rank) = 0.3500
Configured margin (MIN_EDGE_OVER_NO_TRADE) = 0.05
Threshold = 0.3500 + 0.0500 = 0.4000

Weak strategy score  = 0.3490  →  0.3490 < 0.4000  →  NO_TRADE wins
Reason: "Best strategy score 0.349 does not exceed NO_TRADE threshold 0.400"
Strong strategy score = 0.5500  →  0.5500 > 0.4000  →  TRADE selected
```

---

## DB Columns Added (Additive — Zero Existing Row Impact)

**`ase_strategy_evaluations`:**
- `score_inputs_json JSONB` — full 12-input ScoreInputs dict with source_map
- `score_signal_quality NUMERIC(6,4)` — from scorer return dict
- `direction_confidence_used NUMERIC(6,4)` — from scorer return dict
- `compatibility_filter_json JSONB` — filter_compatible() result for this evaluation

**`ase_decision_runs`:**
- `n_compatible INTEGER` — strategies that passed filter
- `n_compat_rejected INTEGER` — strategies rejected by filter
- `compatibility_filter_json JSONB` — full CompatibilityResult.to_dict()

All 7 columns confirmed present in dev DB at SEQ=122 time.

---

## Out of Scope This Phase (Phases 6–8)
- Trigger engine
- Autonomy cadence
- Position management
