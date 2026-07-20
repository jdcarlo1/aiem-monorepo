---
name: DPL Phase 3 — Reproducibility Replay & Part 1 Remediation
description: Complete state after Part 1 remediation — SEQ=27 sealed, 179 PASS / 3 FAIL (all classified), Part 1 gate PASSED
---

## Current state (SEQ=27, Part 1 Remediation COMPLETE)

**Chain:** 14 entries: SEQ 0 (GENESIS), 15–27. SEQ 1–14 not reconstructed.
**Verifier:** dpl/verify_dpl_phase3.py — C01–C54 (179 PASS, 3 FAIL — all correctly classified)
**PSV:** 9/9 PASS for SEQ=27 (post_seal_verify.sh 4-arg: SEQ CHAIN_FILE INDEX_FILE LOGS_DIR)
**Part 1 report:** dpl/dpl_phase3_part1_remediation_report.txt
**Part 1 gate:** PASSED — all fixable items resolved; 3 FAIL = 1 IMPLEMENTATION_DEFECT + 2 PENDING_LIVE_EVIDENCE

## 3 Remaining Failures (correctly classified)

**C52A_verifier_fixtures_contaminate_prod_namespace** — INTENTIONAL FAIL (IMPLEMENTATION_DEFECT)  
9 oe_decision_replay_inputs rows have is_test_record=FALSE, origin_type=NULL — verifier fixture contamination.  
UPDATE trigger blocks in-place correction. 8 registered in oe_synthetic_row_corrections. 1 (2d03987f) in contamination_registry.json only (FK gap). Root cause fixed: current code uses is_test_record=TRUE.

**C52B_genuine_scheduler_decision_exists** — FAIL (PENDING_LIVE_EVIDENCE)  
No oe_decision_replay_inputs row with origin_type='SCHEDULER' AND alert_id IS NOT NULL.  
**Unblocks:** options-pipeline-scheduler fires Mon–Fri 9:45 AM ET. Next: Mon 2026-07-21 13:45 UTC.

**C52C_genuine_replay_pass** — FAIL (DEPENDENCY_BLOCKED by C52B)  
Will auto-run (double replay + determinism check) once C52B passes.

## 4 External Blockers (unchanged from Phase 3 final)

1. **Independent crypto approval** — C48 correctly PENDING_INDEPENDENT_APPROVAL (approved_at=null).
2. **Current E2E replay** — C52B/C FAIL. Unblocks on first live market-day scheduler run.
3. **Low-privilege DB role** — Replit PG is always postgres. aiem_app role exists (C29 PASS).
4. **Crash-consistency process kill** — Requires isolated test environment.

## Part 1 Remediation items completed

1. approved_at → null + PENDING_INDEPENDENT_APPROVAL (no future timestamp). C54 added with 3 neg controls.
2. test_registry_seq25.json (176 checks, reconciled). last_run_results.json written on every run.
3. 9 contaminated rows documented (8 DB + 1 file-only). contamination_registry.json created.
4. C52 split into C52-A (DEFECT), C52-B (PENDING), C52-C (BLOCKED).
5. Chain count wording: Genesis:1, RUN:13, Total:14. MISSING HISTORY note in C33 print.
6. SEQ=26 registered as defective (INVALID_CMD_INVOCATION, exit 127).
7. evidence_manifest.json v2: 16 files with SHA-256 (manifest_version=2, sealed_seq=27).

## Alert_id=25 classification

LEGACY_UNREPLAYABLE — oe_legacy_decision_cutoff (cutoff_id=1).  
alert_created_at=2026-07-17T14:17Z < enforcement_activation_at=2026-07-19T09:04Z.

## SEQ=22 / SEQ=26 formal classifications

SEQ=22 — INCOMPLETE_COMMAND_CAPTURE (CMD=${1} only, fixed to CMD=${*} in SEQ=23)  
SEQ=26 — INVALID_CMD_INVOCATION (label args passed as CMD, exit 127; re-run = SEQ=27)

## Key files (Part 1 state)

- `dpl/verify_dpl_phase3.py` — C01–C54
- `dpl/engine_integrity_refs.json` — approved_at=null, PENDING_INDEPENDENT_APPROVAL
- `dpl/contamination_registry.json` — 9 contaminated rows documented
- `dpl/test_registry_seq25.json` — SEQ=25 baseline (176 checks, reconciled)
- `dpl/evidence_manifest.json` — v2, 16 files, SHA-256 for all
- `dpl/dpl_phase3_part1_remediation_report.txt` — Part 1 report
- `tools/defective_runs_registry.json` — SEQ=22 + SEQ=26
- `tools/last_run_results.json` — machine-readable live results (179 PASS / 3 FAIL)

## Chain head

SEQ=27  ts_end=2026-07-20T00:24:02Z
archive_sha256=56db725f0e0037cc630b46d9fdfb584f939c34cfd4aec8a14fa3007b2b998ff5

## Part 2 gate condition

Run `bash tools/verified_run.sh` after Mon 2026-07-21 09:45 ET and confirm C52B = PASS.

UPDATE 2026-07-20: B1 FIX applied (origin_type was never written — structural defect not external blocker). oe_contamination_exclusions table created with 9 rows (including 2d03987f). SEQ=28 sealed 179 PASS/3 FAIL/9 PSV PASS. Status: CONDITIONAL (zero impl defects, live trade pending). S5 reconciler written at tools/reconcile_sealed_log.py. S8 independent approval = EXTERNAL_BLOCKER.
