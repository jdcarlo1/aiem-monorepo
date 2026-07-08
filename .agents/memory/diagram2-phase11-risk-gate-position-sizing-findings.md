---
name: Diagram 2 Phase 11 (Risk Gate & Position Sizing) verification findings
description: Static wiring/tool-trace results for Phase 11 — cleanest module result of any phase, plus a real correction to pre-existing TOOL_ALIASES documentation.
---

Scope: 10 modules (pre_decision_risk_gate.py, aiem_risk_guards.py,
portfolio_correlation_risk.py, portfolio_allocator.py, portfolio.py,
position_sizing.py, aiem_position_sizing.py, rl_position_sizer.py,
slippage_model.py, daily_loss_limit.py) + 12 AI tools, per `aiem_registry.py`
PHASE_TOOLS[11]. Verify script:
`artifacts/stock-scanner-api/aiem_phase11_verify.py`.

**Modules: 10/10 VERIFIED_WIRED, 0 gaps — cleanest module result of any
phase to date.** Every module has both a direct import AND genuine
downstream usage (not import-then-unused). Two modules are wired only
through the internal paper-trading pick pipeline
(`_aiem_paper_pick_candidates()`), not any AI tool: `aiem_position_sizing.py`
(kill-switch gate, position sizing, 3:45pm ET pre-close review thread) and
part of `aiem_risk_guards.py`'s usage (the internal gate at
main.py ~L39492-39516, in addition to its 3 AI tools).

**Tools: 10/12 have a real dispatch-map entry under their exact tagged
name; 9/10 are genuinely Phase-11-owned (highest same-phase ratio of any
phase so far — contrast Phase 9's 5/20 and Phase 10's 0/2).** The one
cross-phase tool is `execution_realistic_cost` → `execution_simulator.py`
(Phase 13), despite being tagged to Phase 11.

**2 tool-name gaps, but NOT dead capabilities** (important distinction from
Phase 6's `smart_money_divergence`, which had zero real capability behind
it):
- `portfolio_correlation_risk` has no dispatch key of its own; the real
  capability is exposed under `check_portfolio_concentration` (already one
  of the 12 tags), which directly calls this module's
  `check_current_portfolio_risk()`.
- `rl_position_sizer` has no dispatch key of its own; real capability is
  exposed under `rl_get_paper_action` and `rl_readable_policy` only.

**Correction made to pre-existing `aiem_registry.TOOL_ALIASES`** (this
dict already existed from earlier work but had never been checked against
actual main.py source — both entries were wrong or unverified):
- Old guess: `portfolio_correlation_risk` → `portfolio_circuit_breaker_status`.
  **Wrong** — that tool is a totally different mechanism
  (`aiem_risk_guards.py`'s `PortfolioCircuitBreaker`). Corrected to
  `check_portfolio_concentration`, verified by sed trace.
- Old list: `rl_position_sizer` → 6 tools including `rl_status`,
  `rl_strategy_weights`, `rl_ppo_policy`, `rl_counterfactuals`. **Wrong** —
  those 4 actually call `aiem_rl_engine.py` (Phase 15, a completely
  different, larger module); `rl_counterfactuals` is inline direct-SQL with
  no module import at all. Corrected list is only
  `rl_get_paper_action`/`rl_readable_policy`.

Lesson: don't trust an existing `TOOL_ALIASES`/similar "already documented"
dict at face value just because it exists — it can encode unverified
guesses. Always re-derive real ownership by tracing the actual dispatch
function body with sed, even for entries that look pre-resolved.

`correlation_guard_status`/`liquidity_filter_status`/
`portfolio_circuit_breaker_status` are legitimately double-tagged across
`PHASE_TOOLS[2]` and `PHASE_TOOLS[11]` (Phase 2's memory already flagged
these as aiem_risk_guards.py-owned cross-phase tools; Phase 11 confirms
same-phase ownership directly since aiem_risk_guards.py is itself a
Phase 11 module).
