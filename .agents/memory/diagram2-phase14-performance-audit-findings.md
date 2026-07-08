---
name: Diagram 2 Phase 14 (Performance Audit) verification findings
description: First phase with 0/N same-phase tool ownership despite solid module wiring — both audit modules are cron/admin-route-only, invisible to the AI tool layer; new "table-level coupling" category identified.
---

Scope: 4 modules (aiem_performance_auditor.py, aiem_pipeline_audit.py,
aiem_process_backtest.py, signal_outcomes.py) + 11 AI tools, per
`aiem_registry.py` PHASE_TOOLS[14]. Verify script:
`artifacts/stock-scanner-api/aiem_phase14_verify.py`.

**Modules: 3/4 VERIFIED_WIRED, 1/4 VERIFIED_NOT_WIRED_BY_DESIGN, 0 genuine
gaps.**
- `aiem_performance_auditor.py`: wired only into the Sunday `loop_a_research`
  audit-session lifecycle + startup route install + deferred startup check.
- `aiem_pipeline_audit.py`: wired into paper-trade `PipelineTrace`, 4PM MTM
  `log_outcome_for_trade()`, `log_learning_updates()`, and 4
  ADMIN_TOKEN-gated Flask routes.
- `signal_outcomes.py`: genuinely wired (init/store/get/update all called
  from real sites).
- `aiem_process_backtest.py`: **VERIFIED_NOT_WIRED_BY_DESIGN** — standalone
  script with its own `__main__` block, zero imports in `main.py`; only
  referenced in `aiem_master_orchestrator.py`'s manifest (itself confirmed
  not live-wired, per this project's rule) and in `aiem_registry.py`'s
  phase map. Second confirmed standalone-backtest module after the Phase 7
  cluster — matches `backtest-delegation-rule.md` (backtests are run
  manually / by AIEM, never wired into the live app).

**Tools: 11/11 registered (0 dispatch gaps), but 0/11 genuinely owned by
any Phase 14 module** — first phase in the entire sweep with exactly zero
same-phase tool ownership, despite the phase's own modules being solidly
wired elsewhere. Both real Phase 14 modules
(`aiem_performance_auditor.py`, `aiem_pipeline_audit.py`) are reachable
**exclusively** through background cron/loop calls and ADMIN_TOKEN-gated
HTTP routes — an AI chat session cannot query its own audit trail or
trigger a pipeline-integrity check; only the human owner (admin token) or
the scheduled Sunday loop can. Breakdown of the 11 tools: 6 are pure inline
SQL with no module tie at all (`analyze_independent_performance`,
`compare_picks_vs_misses`, `query_pick_outcomes`,
`query_own_prediction_performance`, `review_own_accuracy`,
`analyze_missed_movers`); 4 import a real cross-phase module
(`decision_quality_summary`→`decision_logger.py`/Phase 9,
`eval_window_history`→`evaluation_windows.py`/Phase 9,
`safe_learning_stats`→`safe_learning.py`/Phase 15,
`signal_layer_redundancy`→`signal_correlation.py`/Phase 9); 1
(`shadow_stats`) is dual-tagged with Phase 13, already traced there
(`shadow_ledger.py`/Phase 2).

**New category: "table-level coupling."** `analyze_missed_movers` queries
the `signal_outcomes` DATABASE TABLE that `signal_outcomes.py`'s own
`init_signal_outcomes_table()`/`store_bull_flow_signals()` create and
populate — a genuine downstream-consumer relationship, but via a shared
table rather than a Python import. This is distinct from both "same-phase
ownership" (real import) and "naming trap" (misleading name); when a phase
audit finds a tool that queries a table owned by one of the phase's own
modules without importing it, classify it as table-level coupling, not as
either of the other two categories.
