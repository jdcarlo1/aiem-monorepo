---
name: Diagram 2 Phase 17 Verification & Observability findings
description: Final phase (17 of 0-17) of the AEIM Diagram 2 master wiring sweep — 12 modules, 10 tools, lowest live-AI-tool footprint by design, one genuine architectural gap (drift_alarm.py), one confirmed broken out-of-catalog import.
---

# Phase 17 — Verification & Observability (FINAL PHASE of the 0-17 sweep)

Script: `artifacts/stock-scanner-api/aiem_phase17_verify.py`. Static grep/sed only, never live-imports main.py. Applied to DB (`aiem_module_registry` + `aiem_tool_registry`), confirmed via SQL.

## Module wiring: 12/12 classified, 0 genuine gaps

- **2/12 VERIFIED_WIRED**: `aiem_verification.py` (log_research_loop_run() called from 2 real sites), `aiem_v3_verification.py` (run_full_verification() called from a 4:55 PM Mon-Fri scheduled job AND an admin-token-gated on-demand route).
- **9/12 VERIFIED_NOT_WIRED_BY_DESIGN**: `strict_aeim_supervisor_verifier.py`, `strict_observability_supervisor_verifier.py`, `verify_aiem_loop.py`, `verify_eod_learning_loop.py`, `verify_ml_infrastructure.py`, `verify_premarket_system.py`, `verify_signals.py`, `monitor.py`, `fix_silent_excepts.py`. Each independently confirmed via its own docstring/run-instructions stating manual shell execution, plus zero import hits in main.py. These are intentionally standalone human-run verification tools, not meant to be autonomously callable — a self-verifying AI grading its own pipeline via its own tool call would undermine the independence the verification exists to provide. Matches `verification-script-pattern.md`.
- **1/12 ARCHITECTURAL_REMEDIATION_REQUIRED (new category)**: `drift_alarm.py` — "imported-but-functions-unused, shadow-implemented inline". The module IS imported (`import drift_alarm as _drift_alarm`) and truthy-checked before a scheduled drift job runs, but its two real functions (`compute_drift`, `check_all_active_signals` — both Fisher's-exact-test based per the module's own docstring) are NEVER called anywhere in main.py. The scheduled job instead reimplements a much weaker inline check: a raw win-rate gap >= 10pp threshold against a hardcoded `_baselines` dict, with no statistical significance test at all. Unlike DOCUMENTED_DORMANT (explicitly disabled) or VERIFIED_NOT_WIRED_BY_DESIGN (never meant to be imported), this module's own docstring explicitly WANTS integration — main.py imports it but bypasses its actual logic. Confirmed via exhaustive grep: zero hits for `_drift_alarm.` (method call) and zero hits for `compute_drift`/`check_all_active_signals` anywhere in main.py.

## Tool dispatch: 4/10 registered, 0/4 same-phase-owned

- **6/10 genuine dispatch gaps**: `verify_aiem_loop`, `verify_eod_learning_loop`, `verify_ml_infrastructure`, `verify_premarket_system`, `verify_signals`, `drift_alarm` — none appear ANYWHERE in main.py, not even as a bare string. The registry's PHASE_TOOLS list names these as AI-callable tools that were never actually implemented as such; they exist only as the standalone scripts / inline cron job.
- **4/10 registered, but ALL cross-phase or inline (0 same-phase)**:
  - `simulation_audit_trail` → `simulation_lock.py` (Phase 2)
  - `decision_quality_summary` → `decision_logger.py` (Phase 9)
  - `model_version_history` → `online_learning.py` (Phase 15)
  - `run_statistical_significance` → inline bootstrap resampling test in main.py, no external module ("inline-no-tie")
- Ties Phase 14 for lowest same-phase tool ownership (0%), but for a structurally defensible reason (see module wiring notes above), not a wiring failure.

## Out-of-band finding (not in the 12-module catalog or 10-tool list — no DB row, documented here only)

`aiem_verification_and_trading_brain.py` is imported by two live admin-token-gated Flask routes (`/stock-api/aiem/verification/challenge`, `/stock-api/aiem/verification/verify`) via `from aiem_verification_and_trading_brain import issue_challenge/verify_response`. The file is a real 24KB module but exists ONLY at the repo root (`/home/runner/workspace/aiem_verification_and_trading_brain.py`), never inside `artifacts/stock-scanner-api/` where main.py actually runs and where main.py's own `sys.path.insert(0, dirname(__file__))` (L59) points. A static import-resolution simulation replicating main.py's exact sys.path setup (never a live import of main.py itself) confirms `ModuleNotFoundError`. **Both routes will always 500.** This is the sweep's second confirmed real-but-out-of-catalog module (after Phase 12's `holding_period_optimizer.py`) but the first one that is actively broken rather than merely uncatalogued.

## Sweep-completion note

Phase 17 is the last phase (0-17) of the AEIM Diagram 2 master wiring sweep. All 18 phases now have a corresponding `aiem_phaseN_verify.py` script, applied to the DB and confirmed via SQL.
