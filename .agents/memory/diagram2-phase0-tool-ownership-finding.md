---
name: Diagram 2 Phase 0 tool-ownership finding
description: Why AI-callable tool implementations often don't live in any of the 195 registered Diagram-2 module files, and the registry convention adopted for it.
---

During Phase 0 verification of the AEIM DIAGRAM 2 (18-phase, Phase 0-17) wiring
project, traced all 8 Phase-0-tagged AI tools to their real implementation and
found 0/8 call into any of the 10 Phase 0 module files (scanner.py,
composite_scan.py, multiday_runner.py, etc). All 8 are inline functions
defined directly in main.py, reading/writing tables main.py itself owns
(conviction_stack_watchlist via the L1-L8 `_run_five_layer_conviction` engine,
aiem_independent_picks via `_indep_scan_thread`, polygon_market_daily/
ticker_meta via the mkt_* full-market research engine).

**Why:** Many AI-callable tools in this codebase are thin wrapper functions
that query/compute inline in main.py rather than delegating to one of the 195
Diagram-2 module files. Module-file ownership (the 195-module registry) and
AI-tool implementation location are two separate layers — a tool can
conceptually belong to a phase (by what data/purpose it serves) while its
code lives entirely outside that phase's file modules.

**How to apply:** When doing Tool→Module verification for any phase, do not
force a tool into one of that phase's file modules just because it's
phase-tagged. Trace the real DB writes/reads and record `owning_module` as
either a real module file OR "inline main.py — <function name(s)>" with the
actual write-path evidence. This pattern likely recurs in later phases
(Phase 9 Scoring, Phase 4 Discovery, etc. also have heavy main.py-inline
logic per prior memory notes on _run_five_layer_conviction, mkt_* tools,
and aiem_independent_picks) — expect it, verify honestly each time, and
never round it up to "module_verified: <phase-N-file>" without real trace
evidence like this.

Registry mechanics: `aiem_tool_registry.tool_verification_level` moves from
default `'phase_only'` to `'module_verified'` once traced (regardless of
whether the true location is a file module or inline main.py) —
`'module_verified'` means "verified", not "maps to a phase-file module."
Verification script pattern for this: `aiem_phase0_verify.py` in
artifacts/stock-scanner-api/ (grep-based, no live import of main.py).
