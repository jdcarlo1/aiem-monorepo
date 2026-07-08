---
name: AEIM DIAGRAM 2 — Phase 15 (Learning & Adaptation Loop) findings
description: Verification results for Phase 15 of the AIEM master wiring/verification sweep — largest phase yet (10 modules, 27 tools); best same-phase tool-ownership ratio since Phase 11; first same-phase table coupling found.
---

Phase 15 covers 10 modules: aiem_closed_loop_learning.py, aiem_rl_engine.py,
deep_rl_policy.py, safe_learning.py, online_learning.py,
meta_learning_signal_trust.py, aiem_v3_learning.py, aiem_module2_decay.py,
aiem_module3_promotion.py, aiem_module4_gate.py — largest phase in the sweep.

**Module wiring: 10/10 VERIFIED_WIRED, 0 gaps.** Every module has multiple
real call sites, not just a bare import (e.g. aiem_module2/3/4 form the
M2/M3/M4 gate-integrity pipeline; online_learning.py runs two separate
weight streams — discovery_cycle_signal_weights and
conviction_layer_weights — from different call sites).

**Tool tracing (27 tools, all 27/27 registered in dispatch map):**
- 13 code-owned same-phase (safe_learning_* x3, trust_* x5, deep_rl_* x2,
  rl_ppo_policy/rl_status/rl_strategy_weights x3).
- 1 same-phase table-coupled: rl_counterfactuals is inline SQL on the
  `rl_counterfactuals` table, which aiem_rl_engine.py (a real Phase 15
  module) creates and populates — no import, but stays phase-consistent.
  This is the first *same-phase* table coupling found in the sweep, as
  distinct from Phase 14's cross-phase table coupling
  (analyze_missed_movers → signal_outcomes.py in a different phase).
- 9 cross-phase: adaptive_layer_evaluate/history → aiem_intelligence_layer.py
  (Phase 1); rl_get_paper_action/rl_readable_policy → rl_position_sizer.py
  (Phase 11 — independently reconfirms the earlier Phase 11 TOOL_ALIASES
  correction); check_shadow_promotion → shadow_ledger.py (Phase 2);
  gate_history → pre_decision_risk_gate.py (Phase 11); retrain_pending/
  approve/reject → automated_retrain_pipeline.py (Phase 8).
- 4 inline, no module tie: test_new_signal (scipy/numpy, no import),
  test_scoring_hypothesis (inline SQL on ai_short_calls_log),
  get_bh_fdr_status (inline SQL on aiem_signal_discoveries),
  rollback_to_previous_model (inline SQL, research_date-based).

**Why this matters:** 48% same-phase code ownership (13/27), or 52%
counting the table coupling, is the best ratio since Phase 11 — far above
Phase 12/13/14's single-digit percentages. Confirms this project's naming
convention ("tool name suggests owning module") is only a weak signal;
always trace the actual `import` inside the tool function body before
asserting phase ownership.

Verification script: `artifacts/stock-scanner-api/aiem_phase15_verify.py`
(run pattern matches Phase 13/14 scripts — dry-run without
AIEM_DATABASE_URL, apply with it set). Applied to DB: 10 module rows in
aiem_module_registry (all VERIFIED_WIRED), 27 tool rows in
aiem_tool_registry (all VERIFIED_REAL_IMPLEMENTATION).
