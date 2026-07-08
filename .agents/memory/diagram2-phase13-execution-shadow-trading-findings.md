---
name: Diagram 2 Phase 13 (Execution & Shadow Trading) verification findings
description: Static wiring/tool-trace results for Phase 13 — first DOCUMENTED_DORMANT module of this sweep, plus confirmation that three parallel simulated-trading subsystems coexist.
---

Scope: 4 modules (execution.py, execution_simulator.py, pnl.py,
position_reconciler.py) + 11 AI tools, per `aiem_registry.py`
PHASE_TOOLS[13]. Verify script:
`artifacts/stock-scanner-api/aiem_phase13_verify.py`.

**Modules: 3/4 VERIFIED_WIRED, 1/4 DOCUMENTED_DORMANT, 0 genuine gaps.**
- `execution.py` + `pnl.py`: both genuinely wired, but into a completely
  separate, simple manual "prop trading" simulator
  (`/stock-api/prop/*` Flask routes — buy/sell/reset), NOT the AI
  paper-trading pipeline and NOT the shadow ledger. Confirms **three**
  parallel simulated-trading subsystems coexist in this codebase: AI paper
  trades (`aiem_paper_trades`), the shadow ledger (`shadow_ledger.py`), and
  this manual prop simulator.
- `execution_simulator.py`: wired both via the `execution_realistic_cost`
  AI tool and inline in the paper-trading pick pipeline
  (`fixed_spread_slippage()` on nano-cap fills).
- `position_reconciler.py`: **DOCUMENTED_DORMANT** — first one found via an
  explicit, dated ("as of 2026-07-01") "STATUS: INTENTIONALLY DISABLED —
  DO NOT FIX THIS" docstring rather than inference. `reconcile_positions()`
  is never called anywhere (grep-confirmed across every .py file); the
  module explains why: no real brokerage positions API exists anywhere in
  this codebase (Tradier tokens here are market-data-only), and the only
  available position source is a permanently-fake mock that would trip
  `pre_decision_risk_gate.py`'s mismatch gate on every run (this already
  happened once, 2026-06-28, cleared 2026-07-01). Cross-references the
  pre-existing `risk-gate-enforcement-gaps.md` memory finding — this phase
  pass is the first to formally register it in `aiem_module_registry`.

**Tools: 11/11 registered (0 dispatch gaps), but only 1/11
(`execution_realistic_cost`) is genuinely Phase-13-owned** — second-lowest
same-phase ratio in the sweep (above only Phase 10's 0/2). Unlike prior
naming traps (Phase 6/9/10/12), none of the other 10 are mislabeled — they
are accurately named for their real implementations, just catalogued under
Phase 13 because they're conceptually shadow-trading/evaluation related:
`open_shadow_trade`/`close_shadow_trade`/`start_shadow_window`/
`shadow_stats` → `shadow_ledger.py` (Phase 2); `start_eval_window`/
`close_eval_window`/`is_eval_window_active` → `evaluation_windows.py`
(Phase 9); `safe_learning_log_trade` → `safe_learning.py` (Phase 15);
`simulation_audit_trail` → `simulation_lock.py` (Phase 2);
`record_decision_outcome` → `decision_logger.py` (Phase 9). Call this
"broad phase tagging" as a distinct category from "naming trap" going
forward — the tool name is honest, just the phase tag is loose.
