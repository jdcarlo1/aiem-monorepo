---
name: Diagram 2 Function Registry convention
description: Joel's requirement that every significant inline function in main.py (or any large orchestration file) gets its own auditable registry row, separate from the Module/Tool registries.
---

## Rule
When a phase-tagged AI tool's real implementation turns out to be inline logic in `main.py` rather than one of the 195 registered module files, that inline function (and any inline function it directly depends on) must get its own row in `aiem_function_registry`, not just a note in the tool registry.

**Why:** Joel's explicit instruction (2026-07-08) after the Phase 0 finding that AI tools tagged to a phase are often implemented inline in main.py: "so that nothing is hidden inside a large source file." He wants every important function individually traceable and auditable, with no fabricated module mappings.

**How to apply:**
- Table: `aiem_function_registry` (DDL lives in `aiem_registry.py`, created via `init_schema()`). Columns match Joel's required fields exactly: file_name, function_name, purpose, inputs, outputs, upstream_dependencies, downstream_dependencies, owning_phase(+name), owning_module, is_inline, verification_status, verification_evidence, verified_by_command, plus versioning/timestamps.
- `owning_module` for inline functions is literal text like `"INLINE (main.py) — no dedicated Phase X module file"` — never force-map to one of the 195 real module files.
- `verification_status` has two honest tiers for inline functions:
  - `VERIFIED` — function read in full; every upstream/downstream edge in the row is confirmed.
  - `VERIFIED_EXISTS` — function confirmed real (non-stub) and its own direct effect confirmed, but its *own* deeper upstream chain belongs to a phase not yet reached (e.g. `_run_conviction_scanner`'s L1-L8 signal inputs are Phase 9 work) — note this explicitly in `upstream_dependencies` rather than faking the trace.
- Build/population pattern: one script per phase, e.g. `aiem_function_registry_build.py` (Phase 0), each with a `PHASEn_FUNCTIONS` list of dicts + `upsert_functions()` using `ON CONFLICT (file_name, function_name) DO UPDATE` (bumps `verification_version`). Mirrors the existing `upsert_modules`/`upsert_tools` pattern in `aiem_registry_build.py`.
- Also register *shared inline helpers* called by multiple tools (e.g. `_mkt_parse_conditions`, `_mkt_run_two_group`) even though they aren't tools themselves — they're "significant logic," so they qualify.
- This pattern will recur in most/all of the remaining 17 phases wherever inline main.py logic does real work; budget time for it in every phase's verification pass, not just Phase 0.
