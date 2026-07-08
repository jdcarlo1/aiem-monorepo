---
name: AEIM Diagram 2 Phase 2 (Guardrails & Safety) findings
description: Verification results and gotchas for Phase 2 of the 18-phase AEIM master wiring/verification project — module wiring, tool cross-phase ownership, transitive wiring proof pattern.
---

## Result
0 genuine module-wiring gaps (better than Phase 1, which had aiem_master_orchestrator.py
orphaned). All 10 Phase 2 module files are either wired or unwired strictly by design.

## Transitive wiring proof pattern
`order_dedup.py` has ZERO direct references in main.py, yet is genuinely wired: it is
imported by `pre_decision_risk_gate.py` (Phase 11) and `premarket_open_trader.py`
(Phase 0), and main.py imports both of those carriers directly. Proof requires TWO greps
per carrier: (1) carrier imports the target module, (2) main.py imports the carrier.
A module with zero direct hits in main.py is not automatically an orphan — check its
reverse-dependents (`grep -rn "import order_dedup" --include=*.py`) before concluding a gap.

## By-design orphans (not gaps)
`lookahead_audit.py` and `manual_rollback.py` are standalone CLI tools whose own
docstrings explicitly say so (lookahead_audit: static-audit script meant to be run
directly; manual_rollback: deliberately manual, not auto-fired, to keep a human in the
loop before reverting a model). Absence of any repo reference + explicit docstring intent
= `VERIFIED_NOT_WIRED_BY_DESIGN`, not a failure.

## Tool ownership surprise: guardrail tools live in Phase 11
Of the 11 Phase-2-tagged AI tools, only 5 are genuinely Phase-2-file-owned
(point_in_time_guard.py, simulation_lock.py, kill_switch.py). The other 6:
- `check_signal_data_availability` → real module, but it's `aiem_pullback_reentry.py`
  (Phase 5) — a narrow one-off bear-market signal check, not a general guardrail despite
  the generic name.
- `correlation_guard_status`, `liquidity_filter_status`, `portfolio_circuit_breaker_status`,
  `portfolio_circuit_breaker_reset` → all four real, but owned by `aiem_risk_guards.py`
  (Phase 11), not Phase 2. The live correlation/liquidity/circuit-breaker guardrail
  surface is implemented entirely in a Phase 11 file.
- `mkt_check_survivorship` → fully inline in main.py, direct psycopg2 query on
  `ticker_lifecycle`, no module file at all — required its own Function Registry row
  (Phase 0's `_mkt_refresh_ticker_lifecycle_bg` populates that table weekly).

**Lesson**: a tool's phase tag and its name describe intent/category, not actual code
ownership. Always trace the handler body to the real import before crediting a phase
with owning a tool.

## Script/registry pattern
`aiem_phase2_verify.py` follows the same shape as `aiem_phase1_verify.py`: static grep/sed
only, `_NON_WIRING_FILES` exclusion list, updates both `aiem_module_registry` and
`aiem_tool_registry`. `aiem_function_registry_build.py` gained a `PHASE2_FUNCTIONS` list
(only inline, no-module-file functions need a row — file-owned or cross-phase-real-module
tools don't).
