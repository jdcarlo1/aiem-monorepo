---
name: Function registry AST source
description: How aiem_function_registry is populated — AST scan, no static dict analog, key design decisions
---

## Rule
`aiem_function_registry` is populated by an AST scan of the 194 files in `MODULE_PHASE_MAP` (via `build_function_rows()` in `aiem_registry_build.py`). There is no static data structure in `aiem_registry.py` that is the function-inventory equivalent of `MODULE_PHASE_MAP` (modules) or `PHASE_TOOLS` (tools). `DIAGRAM2_STAGE_MAP` was evaluated and rejected — its `runtime_function` strings are descriptive annotations, not clean Python identifiers.

**Why:** No authoritative function inventory existed; Path B (AST scan) was chosen over Path A (DIAGRAM2_STAGE_MAP) and Path C (new static dict) because it produces clean, greppable Python function names directly from source without requiring new data authoring.

**How to apply:**
- When re-running the build after adding new files to `MODULE_PHASE_MAP`, `upsert_functions()` will pick them up automatically on next build run.
- `purpose`, `inputs`, `outputs`, `upstream_dependencies`, `downstream_dependencies` are all NULL — no source for these fields exists. Leave them for future annotation.
- Dry-run predicted 1727; live DB has 1693. Delta of 34 = duplicate `(file_name, function_name)` pairs caused by Python function redefinitions within the same file. This is correct and proven: `1727 - 34 = 1693`.
- 4 files produce 0 rows by design: `backtest_options.py`, `aiem_probability_engine/__init__.py`, `aiem_probability_engine/config.py`, `aiem_probability_engine/walk_forward.py`.
- `main.py` excluded by `MPM_EXCLUDE` — same exclusion as `aiem_module_registry`.
- `is_inline=True` means parent AST node is `ClassDef` or `FunctionDef/AsyncFunctionDef` (class methods + nested closures). Module-level defs are `is_inline=False`.
- Dunders (`__init__`, etc.) are included — 25 rows carry dunder names.
