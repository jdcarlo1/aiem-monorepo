---
name: AEIM DIAGRAM 2 Phase 8 — ML / Probability Engine findings
description: How aiem_probability_engine was proven wired without ever being Python-imported by main.py, plus the 9 by-design standalone tools inside it.
---

Phase 8 covers 28 modules (ml_engine.py, ml_infrastructure.py, model_training.py,
feature_engineering.py, the alpha_* training stack, the retrain pipelines, and
the 18-file `aiem_probability_engine/` package) and 16 AI tools. Verified via
`artifacts/stock-scanner-api/aiem_phase8_verify.py` (run with
`AIEM_DATABASE_URL=$DATABASE_URL python3 aiem_phase8_verify.py`): 19/28
VERIFIED_WIRED, 9/28 VERIFIED_NOT_WIRED_BY_DESIGN, 0 genuine gaps; 16/16 tools
registered, 0 gaps.

**Key pattern — "isolated but running" is a real, provable state.**
`aiem_probability_engine/__init__.py` documents an explicit isolation contract:
never Python-imported by main.py, never sharing a scheduler/thread pool. That
contract is real (repo-wide grep for `import aiem_probability_engine` /
`from aiem_probability_engine.` = zero hits) — but the package is NOT dormant.
It has its own dedicated, currently-running production workflow
(`probability-engine-scheduler` in `.replit`, running `daily_scheduler.py`
directly, confirmed independent of main.py) and main.py calls two of its
scripts (`daily_picks.py`, `live_query.py`) as arm's-length OS subprocesses
(`cwd=` the package dir), never as Python imports. Static import-grep on
main.py alone would have wrongly flagged the whole package as orphaned.

**Why this matters for future phases:** whenever a module cluster claims an
"isolation contract" or "never imported by main.py" in its own docstring,
check for (a) its own registered workflow in `.replit`, and (b) subprocess
invocation (`Popen`/`subprocess.run` with a `cwd=`) before concluding it's
unwired. Both are real, independently-verifiable wiring paths that a plain
`import X` grep will miss entirely.

**The 9 by-design cluster inside aiem_probability_engine**: `__init__.py`
(inert docstring only), `calibration.py`, `train.py`, `walk_forward.py` (manual
CLI tools, own `__main__`, zero callers), `pit_correction.py` (explicit
one-time re-scoring tool per its own docstring — main.py only reads the table
it writes), `pit_metrics.py` (main.py explicitly "mirrors" its logic instead of
calling it), `date_utils.py` (only callers are the two unwired
calibration.py/walk_forward.py), `verify_live_query.py` (deliberate standalone
external-auditor script). Plus `scripts/spy_historical_backfill.py`
(root-level, one-time, idempotent `ON CONFLICT DO NOTHING` backfill, zero
callers) — same "genuine standalone script" pattern as Phase 7's
backtest_*.py cluster.

**Gotcha hit and fixed during this phase:** `apply_findings_to_registry()`
must derive `module_name` via `os.path.basename(mod)` before stripping `.py`
— the registry's `module_name` column is always a bare basename
(`aiem_registry_build.py` line ~75: `os.path.basename(module_file).replace(".py","")`).
For package-path dict keys like `"aiem_probability_engine/daily_scheduler.py"`,
naively doing `mod[:-3]` leaves the directory prefix in place and the `UPDATE
... WHERE module_name = %s` silently matches zero rows (no error — just a
no-op). Always verify a registry-writing UPDATE by re-querying the DB
immediately after running, not just by trusting the script's own printed
"REGISTRY UPDATED" line.
