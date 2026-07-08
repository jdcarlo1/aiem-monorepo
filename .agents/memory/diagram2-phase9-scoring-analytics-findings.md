---
name: Diagram 2 Phase 9 — Scoring, Analytics & Decision Logging findings
description: Verification results and key traps for the 11-module/20-tool Phase 9 wiring pass in the AEIM DIAGRAM 2 project (stock-scanner-api)
---

Phase 9 is the cleanest module-wiring result of the whole Diagram 2 project so far: all 11 modules (scoring.py, analytics.py, ensemble_combiner.py, pre_recommendation_synthesis.py, signal_correlation.py, signal_magnitude_analysis.py, evaluation_metrics.py, evaluation_windows.py, prediction_logger.py, decision_logger.py, decision_logging_helper.py) are genuinely wired — 0 gaps, 0 VERIFIED_NOT_WIRED_BY_DESIGN. 9 are direct imports into main.py; 2 (scoring.py, evaluation_metrics.py) are transitive, each reachable via **two independent** directly-wired carrier modules spanning different phases.

All 20 Phase-9-tagged AI tools are genuinely registered in the dispatch map (0 gaps), but only 5/20 (25%) are actually backed by a Phase 9 module file — the lowest module-ownership ratio of any phase. 2 are cross-phase module-owned (alpha_score_ticker → alpha_historical_trainer.py Phase 8; strategy_ensemble → aiem_level3.py Phase 1). The remaining 13 are inline ad-hoc SQL/analytics functions in main.py with no owning module — now logged individually in aiem_function_registry (PHASE9_FUNCTIONS).

**Naming trap:** the tool `analyze_signal_correlation` is 100% inline and has zero relationship to the real `signal_correlation.py` module. That module IS wired, but via a completely different, non-Phase-9-tagged function (`_aiem_tool_signal_layer_redundancy`). Same shape as Phase 5's `mkt_compute_indicators`-is-not-`indicators.py` and Phase 6's `smart_money_divergence` traps — never assume a tool name implies its same-named module is the real implementation; always trace the function body.

decision_logger.py and evaluation_windows.py both also get `.init_schema()` called at startup in the aiem_integrity bootstrap block, matching the general startup-init pattern used by other integrity modules — strong corroborating evidence beyond the import grep alone.

Script: `artifacts/stock-scanner-api/aiem_phase9_verify.py` (same structure as phase8's template — DIRECT_WIRED_MODULES / TRANSITIVE_WIRED_MODULES / PHASE9_TOOLS dicts, apply_findings_to_registry using os.path.basename module-name fix).
