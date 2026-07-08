---
name: Diagram 2 final closure verdicts (5 architectural gap items)
description: Fix-or-justify verdicts for the 5 items required to mark AEIM Diagram 2 COMPLETE — Master Orchestrator, Probability Engine bypass, Bull/Bear persistence, Thompson Sampler persistence, 13-stage-to-18-phase mapping. Full detail in artifacts/stock-scanner-api/DIAGRAM2_FINAL_CLOSURE_VERIFICATION.md.
---

All 5 items CLOSED (2026-07-08). Full report with SQL proof and diffs:
`artifacts/stock-scanner-api/DIAGRAM2_FINAL_CLOSURE_VERIFICATION.md`.

- **Master Orchestrator** (`aiem_master_orchestrator.py`) — real 1550-line class, registry VERIFIED_EXISTS Phase 1, zero live callers outside its own test block. Human decision (Joel) was to leave unwired. Documentation-only closure, no code change.
- **Probability Engine bypass** — `aiem_probability_engine/` runs live on its own workflow (`probability-engine-scheduler`) but `main.py`'s live trade-decision path never consults its predictions; only a read-only mirror + admin force-run subprocess exist. This is a documented, intentional isolation contract (package's own `__init__.py`), not a bug. No code change.
- **Bull/Bear debate persistence** — was called live every time (Phase 10, 100% wired) but its output was never durably written. Fixed: rewrote `bull_bear_debate.py: init_schema()` to match live schema + added `persist_debate()`; wired a new persistence block into `main.py` after paper-trade insert. **Lesson: "wired/called" and "output persisted" are different verification questions — a module can pass one and fail the other.**
- **Thompson Sampler persistence** — already closed in an earlier session with DB proof; re-confirmed via registry (Phase 15, `aiem_closed_loop_learning.py`/`aiem_rl_engine.py` both 10/10 VERIFIED_WIRED).
- **13 runtime audit stages → 18 Diagram-2 phases** — all 13 `_MODULE_ORDER` stages are logged by one shared instrumentation module (`aiem_pipeline_audit.py`, itself Phase 14); stages 1-12 are called inline from `main.py` (Phase 1 as *call site*), stage 13 from `aiem_closed_loop_learning.py` (Phase 15). Functional phase often differs from call-site phase (e.g. stage 4/8 nominally route through `drift_alarm.py`/Phase 17, but its real Fisher-test functions are never invoked — same known gap as the separate drift_alarm `ARCHITECTURAL_REMEDIATION_REQUIRED` finding). Only 6 of 18 phases have direct stage coverage; the rest are upstream/offline-by-design, the known Phase-8 bypass, or narrow out-of-scope gaps (documented, not fabricated).
