---
name: DPL Phase 3 — Reproducibility Replay & Institutional Audit Remediation
description: R8 complete; SEQ=44 is the clean baseline: 189P/6F (all 6 are external blockers or PENDING live evidence); chain intact through SEQ=44
---

## Current state (post R8)

**Chain head:** SEQ=44
**SEQ=44 results:** 189 PASS, 6 FAIL — post-seal 9/9 PASS
**Total checks:** 195 (189+6)
**clean_sealed_runs list:** SEQ=42 and SEQ=44 are clean (TREE=DIRTY is expected in Replit — Replit auto-commits only at task end)

## 6 Expected Failures at SEQ=44 (all external/PENDING)

| Check | Classification | Unblocks When |
|---|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER | Independent reviewer provides approved_by + approved_at |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER | Same — reviewer identity in allowlist |
| C52B_scheduler_origin_decision_exists | PENDING | options-pipeline-scheduler Mon–Fri 09:45 AM ET |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE | Scheduler fires AND produces TRADE decision |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED | Blocked by C52B |
| C52C_historical_replay_eligible_row_exists | PENDING | First 9:45 AM ET scheduler run with replay capture |

## R8 Automatable Items — All 8 Complete

| # | Item | File | Status |
|---|---|---|---|
| 1 | Scheduler causal trace (12-stage) | dpl/scheduler_trace.py + aiem_options_scheduler.py | DONE |
| 2 | check_clean_tree.py NUL-delimited allowlist | tools/check_clean_tree.py | DONE |
| 3 | Hash-chained correction ledger | dpl/correction_ledger.py | DONE |
| 4 | oe_classification_correction_ledger quarantine table | dpl/correction_ledger.py | DONE |
| 5 | Expanded C16 evidence (UPDATE+DELETE triggers+defs) | verify_dpl_phase3.py | DONE |
| 6 | Typed ViolationRecord for A8 cascade provenance | verify_dpl_phase3.py | DONE |
| 7 | C52C frozen historical replay | verify_dpl_phase3.py | DONE |
| 8 | Verifier negative controls (NC1/NC2/NC3) | verify_dpl_phase3.py | DONE |

## Key structural decisions

**A8 Layer-1 ordering artifact:** NC1/NC2/NC3 run AFTER A8 Layer-1 enforcement in the script.
By the time A8 Layer-1 evaluates `_PASS`, the NC checks haven't run yet, so they appear as
"removed" from the previous run's pass_list. Fix: NC1/NC2/NC3 added to `_A8_L1_META_EXCL`.
**Why:** same rationale as `A8_baseline_erosion_clean` (Layer-2 checks excluded from Layer-1
evaluation for the same ordering reason). NC checks are still verified normally and appear in
_PASS by end of run.
**How to apply:** any new negative control defined AFTER the A8 enforcement block must be added
to `_A8_L1_META_EXCL` (line ~3585 in verify_dpl_phase3.py). Alternative: move NC checks before
A8 enforcement (requires restructuring; currently deferred).

**C47B allowlist:** dpl/ *.py allowlist now includes correction_ledger.py and scheduler_trace.py
(R8 additions). Any new .py added to dpl/ must be added to this allowlist with a reason comment.

**TREE=DIRTY is expected:** Replit auto-commits at task end; verified_run.sh TREE filter excludes
`??` untracked + `dpl/engine_integrity_refs.json` + `tools/verified_run_seq`. DIRTY is normal.

## Key hash values (SEQ=44)

- `engine_root_hash`: `f34c8d05649e9f5e99632c4a17637d8f35887715d7a64d70829a761b2710d498`
- `commit_sha` in refs.json: `92659130fbd84f4824011f7af94bac1d9b876069`
- `entry_hash` SEQ=44: `7b4edc70e5a4e51ae923674fa365e9ff5de69edd9b18b35f12811d77f4a09d95`

## DB tables added in R8

- `oe_classification_correction_ledger` — hash-chained correction records (153 exceptions, 218 entries)
- `oe_unreplayable_rows` — non-replayable row quarantine registry (2 registered, both not recoverable)
- `oe_scheduler_trace` — 12-stage causal chain for each scheduler run
- `oe_scheduler_trace_stages` — per-stage records linked to trace

## Next unblock conditions

1. **Monday 2026-07-21 09:45 ET:** C52B_scheduler_origin_decision_exists unblocks (first live run)
2. **Any TRADE market day:** C52B_live_trade_decision_exists + C52C + C52C_historical unblock
3. **External reviewer:** C48 + C28_approved_by unblock (must be non-self identity)
4. **After Monday run:** update engine_integrity_refs.json commit_sha to new HEAD if HEAD changed
