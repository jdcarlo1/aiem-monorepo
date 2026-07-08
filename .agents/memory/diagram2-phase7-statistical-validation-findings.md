---
name: Diagram 2 Phase 7 — Statistical Validation & Backtesting findings
description: Module/tool wiring verdicts for the 24 modules + 21 tools in Phase 7 of the AEIM DIAGRAM 2 master wiring verification; explains the large standalone-backtest-script cluster.
---

Phase 7 of the AEIM DIAGRAM 2 verification (24 modules, 21 tools) is closed.
Script: `aiem_phase7_verify.py` (same static grep/sed template as Phase 6, never
imports main.py live). Run with `AIEM_DATABASE_URL="$DATABASE_URL" python3
aiem_phase7_verify.py` from `artifacts/stock-scanner-api/`.

**Modules: 8/24 VERIFIED_WIRED, 16/24 VERIFIED_NOT_WIRED_BY_DESIGN (0 genuine gaps).**
This is the largest non-wired cluster of any phase so far (two-thirds of the
phase), but every one is independently corroborated as a deliberate standalone
research script, not an accidental gap:

- 6 directly wired: backtest.py, benchmark_comparison.py,
  historical_performance.py, layer9_statistical_edge.py, stat_arb_engine.py,
  volatility_clustering.py.
- 2 transitively wired (module-owns-module pattern, same as Phase 5's
  advanced_quant_indicators.py via layer9_statistical_edge.py):
  aiem_stat_tests.py (via aiem_module5_discovery.py + aiem_module6_rediscovery.py,
  both Phase 4 carriers already wired), factors.py (via prop_signal.py, Phase 0
  carrier already wired). Neither has any direct hit in main.py — always check
  transitive callers before calling a "0 hits" module a gap.
- 16 by-design standalone backtest/research scripts (all 15 backtest_*.py
  siblings except backtest.py itself, plus event_study_backtest.py): each has
  a docstring describing a one-off historical analysis with a hardcoded date
  range (e.g. "Jun 2026"), several have explicit "Run: python3 <file>"
  instructions, 10/16 have their own `__main__` block, and none are imported
  by main.py (repo-wide grep confirmed). aiem_registry.py's own
  OWNERSHIP_NOTES already flagged this exact cluster pre-verification. This
  matches memory `backtest-delegation-rule.md` (backtesting is run manually /
  by AIEM, never wired into main.py's live path) — same category as
  fetch_si_background.py (Phase 6) and lookahead_audit.py/manual_rollback.py
  (Phase 2).

**Tools: 21/21 registered with a traced real implementation — 0 gaps, the
cleanest tool result of any phase to date.** 3 Phase-7-owned (stat_arb_check,
mkt_layer9_score, benchmark_vs_baselines), 7 cross-phase module-owned
(run_granger_test→causal_inference.py Phase 4; run_backtest/analyze_metrics/
walk_forward_validate→aiem_level2.py Phase 1; run_aiem_self_backtest→
self_coding_orchestrator.py Phase 1 via `_sco_execute`; run_gspc_full_history_backtest/
run_vix_spike_reversal_grid→aiem_pullback_reentry.py Phase 5;
ml_classification_metrics/ml_regression_metrics→ml_infrastructure.py Phase 8),
11 inline in main.py with no owning module file.

**Why this matters for later phases:** whenever a module shows 0 direct hits
in main.py, check (1) transitive wiring via sibling carrier modules already
verified in another phase, and (2) whether its own docstring/`__main__`
block/hardcoded historical dates identify it as a deliberate standalone
script before concluding it's a genuine gap.
