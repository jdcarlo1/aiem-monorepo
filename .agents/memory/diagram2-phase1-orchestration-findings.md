---
name: Diagram 2 Phase 1 (Orchestration Layer) verification findings
description: Module/tool wiring results for Phase 1 of the AEIM Diagram 2 master wiring project — what's genuinely wired vs orphaned vs cross-phase.
---

Verified via `artifacts/stock-scanner-api/aiem_phase1_verify.py` (run with `AIEM_DATABASE_URL=$DATABASE_URL python3 aiem_phase1_verify.py`).

## Module wiring (12 modules)
11/12 genuinely wired (main.py, self_coding_orchestrator.py, aiem_provenance.py,
aiem_supervisor.py, aiem_intelligence_layer.py, aiem_v2_system.py, aiem_level2.py,
aiem_level3.py, aiem_v3_orchestrator.py, aiem_process.py [own standalone workflow],
aiem_comm_test.py [orphaned BY DESIGN — its own docstring says it's a manual human-run
communication-verification harness, never meant to be imported]).

**Real gap:** `aiem_master_orchestrator.py` (~1550 lines, `AEIMMasterOrchestrator` class)
is a full "wire every AIEM module through one shared AEIMTradePacket" pipeline covering
stages 0-9+ of the entire architecture — built but has ZERO references anywhere else in
the repo (no import, no subprocess, no scheduler). Only its own `LOCAL TEST` `__main__`
block invokes it standalone. This looks like it was meant to be THE master orchestrator
Diagram 2 describes, never activated. Flagged to Joel for a decision (wire it in vs.
leave as documented future work) rather than silently fixed or silently ignored.

## Tool ownership (8 tools) — more nuanced than Phase 0
Phase 0 found ALL 8 tools were inline. Phase 1 is a mix:
- run_level2, run_level3, v2_run_cycle, v2_status: genuinely call into real Phase 1
  module files (aiem_level2.py/aiem_level3.py/aiem_v2_system.py). Correctly Phase-1-owned.
- log_decision, get_decisions: call a REAL module (decision_logger.py) but it's owned
  by Phase 9, not Phase 1 — cross-phase tool->module reference, recorded honestly.
- log_prediction, get_live_snapshot: truly inline in main.py, no module file at all —
  registered in aiem_function_registry (2 rows, both VERIFIED).

## Gotcha for future phase-verify scripts
When grepping the whole repo to prove a module is "unwired" (absence proof), you MUST
exclude the registry/build/verify scripts themselves (aiem_registry.py's
MODULE_PHASE_MAP, aiem_registry_build.py, aiem_function_registry_build.py, and the
verify script's own docstring/dict literals) — they legitimately mention the module's
bare filename as metadata, which is NOT code wiring and will false-positive an orphaned
module as "wired" if not excluded. `_NON_WIRING_FILES` exclusion list in
aiem_phase1_verify.py is the pattern to copy for aiem_phase2_verify.py onward.
