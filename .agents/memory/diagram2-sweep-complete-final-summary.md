---
name: Diagram 2 sweep complete — final summary (all 18 phases, 0-17)
description: Top-level index/summary of the AEIM Diagram 2 master wiring + verification sweep across all 195 modules and 220 tools. Read this first when asked about overall AIEM wiring health; drill into per-phase topic files for detail.
---

# AEIM Diagram 2 — Master Wiring + Verification: SWEEP COMPLETE

All 18 phases (0 through 17) of the static grep/sed-based wiring audit of
`artifacts/stock-scanner-api/` are done. Each phase has its own
`aiem_phaseN_verify.py` script (applied to the DB) and its own
`diagram2-phaseN-*-findings.md` memory file — see the MEMORY.md index for
the full per-phase list. This file is the aggregate rollup.

## Final registry totals (queried directly from `aiem_module_registry` / `aiem_tool_registry`)

**Modules: 195 total**
- 152 VERIFIED_WIRED (78%)
- 39 VERIFIED_NOT_WIRED_BY_DESIGN (20%) — standalone human-run scripts, disabled-by-design daemons, etc.
- 3 DOCUMENTED_DORMANT (behavioral_fingerprint.py, position_reconciler.py, + 1 more) — real code, explicitly deferred via the module's own docstring, never faked as wired
- 1 ARCHITECTURAL_REMEDIATION_REQUIRED (drift_alarm.py, Phase 17) — the only module where main.py imports it but bypasses its real logic with a weaker inline duplicate; this is a genuine bug, not a design choice

**Tools: 220 total**
- 210 VERIFIED_REAL_IMPLEMENTATION (module_verified) (95%)
- 7 VERIFICATION_FAILED (genuine dispatch-map gaps — tool name appears nowhere in main.py's tool dispatch, only as a registry/planning artifact)
- 2 VERIFIED_ALIAS_NOT_DIRECT_DISPATCH
- 1 VERIFIED_INLINE_NO_MODULE (run_statistical_significance — real logic, but inline in main.py with no owning module file)

## Cross-phase patterns discovered during the sweep (see individual phase files for full detail)

- **Naming traps** (tool name resembles a module name but is unrelated): at least 4 confirmed — `analyze_signal_correlation` (Phase 9, really inline), `adversarial_review` (Phase 10, really `adversarial_critique.py`), `query_exit_timing` (Phase 12), `mkt_compute_indicators` (Phase 5, NOT `indicators.py`).
- **Transitive-only wiring**: a module is real and does get exercised, but only via another module's import chain, never a direct main.py import — seen in Phases 1-3 (order_dedup.py, regime.py, regime_macro_patch.py) and Phase 16 (earnings_calendar.py via premarket_open_trader.py).
- **Table-level coupling**: two modules share ownership of the same DB table without a direct code import between them — first seen Phase 14, second same-phase instance Phase 15 (rl_counterfactuals → aiem_rl_engine.py).
- **DOCUMENTED_DORMANT verdicts require the module's OWN docstring/comments as proof** (never inferred) — behavioral_fingerprint.py (Phase 4) and position_reconciler.py (Phase 13, "DO NOT FIX" dated docstring) are the two clean examples.
- **Out-of-catalog but real modules** (exist as files, not in the 195-module registry, discovered incidentally): `holding_period_optimizer.py` (Phase 12, uncatalogued but working) and `aiem_verification_and_trading_brain.py` (Phase 17, uncatalogued AND confirmed broken — lives at repo root, unreachable from main.py's own sys.path, both admin routes that import it always 500).
- **Lowest same-phase tool ownership**: Phase 14 (Performance Audit, 0/11) and Phase 17 (Verification & Observability, 0/4 registered) both hit 0%, but for different, both-defensible reasons — Phase 14's audit modules are simply cron/admin-route-only with no AI-tool surface; Phase 17's verification scripts are intentionally kept outside the AI's own tool-calling loop (self-verification independence).
- **Highest same-phase tool ownership**: Phase 11 (Risk Gate/Position Sizing, 9/10) and Phase 15 (Learning & Adaptation, 13/27).

## Known outstanding items from the sweep (not yet remediated)

1. `drift_alarm.py` (Phase 17) — real Fisher's-exact-test drift functions never called; main.py runs a weaker inline win-rate-gap check instead.
2. `aiem_verification_and_trading_brain.py` (Phase 17, out-of-catalog) — two admin routes always 500 due to a missing file in main.py's actual sys.path directory.
3. Per-phase VERIFICATION_FAILED tool entries (7 total across the sweep) — genuine dispatch-map gaps where a registry tool name has zero presence in main.py; see each phase's findings file for which module they were mistakenly presumed to belong to.

None of these were silently patched during the sweep itself — the sweep's mandate was proof/verification only (no live code changes), so all three remain open items for a future remediation pass if the user wants one.
