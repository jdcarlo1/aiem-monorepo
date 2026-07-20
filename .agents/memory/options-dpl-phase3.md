---
name: DPL Phase 3 — Reproducibility Replay & Institutional Audit Remediation
description: R10 complete; SEQ=46 sealed 08:39 ET; 196P/7F; A32/A33 done (13-field entry_hash); CERTIFICATION_GAP_A28+A30 printed; B23 DEFAULT dropped; TREE=DIRTY (git commit blocked)
---

## Current state (post R10)

**Chain head:** SEQ=46
**SEQ=46 results:** 196 PASS, 7 FAIL — PSV 9/9 PASS
**Total checks:** 203 (196+7)
**SEQ=46 TS:** 2026-07-20T12:39:27Z (08:39 ET — before 09:45 ET scheduler)
**git_commit at seal:** ea2c57bc68dcb82c34887f6d2966b5ed679207b4 (TREE=DIRTY)
**entry_hash:** a93c4d1a0be6ad1124d546de62bb10bb862be5277b03d4a8e83ef57bace762f8
**prev_hash:** 5d034f1ea752fe481fa91433f703eb35aae87bc8d6ffb8b85e81e7af552061c0 (= SEQ=45)
**sha256(log):** ba27991508c942836fd1dcf249e2083271c881c789b4f69f2b3f2382a23d8896
**last_run_results_sha256:** 718480f8eddeb617d420861120ae4451c05397d77f2dbe6c36b21b6f778742de
**a8_l1_excl_sha256:** 4fb064353f8c6fc423284bf45251e2f4b34a5b8c4f9ab281a892365720e11da0
**R10 response doc:** dpl/DPL_Phase3_R10_Audit_Response.md

## R10 remediation completed

- **A32**: sha256(last_run_results.json) + a8_l1_excl_sha256 added to chain entry_hash payload (now 13 fields, was 11); PSV5 PASS confirms schema; both written as named fields in JSONL chain entry
- **A33**: `_A8_EXCL_REGISTRY` added (6 entries, each citing SEQ that justified addition); 2 new checks PASS: `A33_excl_list_registry_complete` + `A33_excl_list_new_names_have_registry_entry`; `A8_L1_META_EXCL_SHA256=4fb064...` emitted in log; `a8_excl_list` + `a8_excl_sha256` added to last_run_results.json
- **A28**: CERTIFICATION_GAP_A28 printed at certification level; refs.json IS committed but has stale commit_sha (0c26566d ≠ HEAD ea2c57b); TREE=DIRTY because R10 edits to verify_dpl_phase3.py + verified_run.sh not committed before seal (git commit blocked in session); TREE=CLEAN not achieved
- **A30**: CERTIFICATION_GAP_A30 printed at certification level; ledger genesis authored by audited party; 218/218 entries share same approved_by='forensic_audit_2026-07-19'; no external witness
- **B23**: `ALTER TABLE oe_unreplayable_rows ALTER COLUMN registered_by DROP DEFAULT` executed; column_default=NULL confirmed

## Critical chain schema note

**Chain entry_hash payload schema v4 (R10, SEQ=46+):** 13 fields (sorted keys):
`a8_l1_excl_sha256`, `cmd`, `commit`, `exit_code`, `last_run_results_sha256`, `log_sha256`, `prev_hash`, `req6_weights_hash`, `scoring_fn_ast_hash`, `seq`, `tree`, `ts`, `ts_end`
PSV5 recomputation uses this schema. Future changes to the payload schema WILL break PSV5 for older entries — do not add fields without documenting the version boundary.

## refs.json fix required before next seal

Before next sealed run: update `dpl/engine_integrity_refs.json` field `commit_sha` to match the current git HEAD SHA (after R10 session-end auto-commit). Current stale value: `0c26566d`. Expected new HEAD: one commit after `ea2c57bc` (the R10 session-end commit).

## Remaining open gaps (R10)

- **A28/C28_refs_commit_sha**: refs.json commit_sha stale (fix before next seal)
- **A31**: 153rd exception — 2 split-state rows (`2d03987f38c44c0bbb2daa73`, `43fc85d578a940069f0dc94d`) are candidates; auditor's 153-id list needed to confirm which one
- **B17**: `oe_contamination_exclusions` referenced only by verifier; no production guard
- **B23 C27 chain resolution check**: evidence_ref → SEQ+sha256 verification not yet implemented in verifier; pending SEQ=47
- **C52B/C52C**: pending Monday 09:45 ET scheduler first live decision

## R9 remediation completed

- **A21**: SEQ=45 pre-run baseline sealed before 09:45 ET scheduler fire ✓
- **A24**: `dpl/engine_integrity_refs.json` removed from `verified_run.sh` allowlist; TREE=DIRTY is now the correct outcome when refs.json is uncommitted
- **A25**: NC4 (`NC4_genuine_removal_still_fires_with_excl_list`) added to verifier + `_A8_L1_META_EXCL`; proof of spurious NC1/NC2/NC3 is from SEQ=43 log (VIOLATION then PASS in same run, independent of exclusion list)
- **A26**: `oe_unreplayable_rows` migration: `evidence_ref SET NOT NULL`, format CHECK `^SEQ=[0-9]+ sha256=[0-9a-f]{64}$`, `registered_by TEXT NOT NULL DEFAULT 'verify_dpl_phase3.py' CHECK IN (...)`. 6 new C27 checks all PASS in SEQ=45.

## R9 disclosure items

- **A22**: Full headers for SEQ=42/43/44 in `dpl/DPL_Phase3_R9_Audit_Response.md`
- **A23**: C52A_verifier_fixtures_contaminate_prod_namespace PASS (not superseded; 0 undocumented prod rows)
- **A27**: Ledger genesis prev_ledger_hash=GENESIS, ledger_hash=ab01356cd8da5b3e0a93c7b8c1731eebf39f90d38f9fcadf34bb0ec3d9e494cd; 218 entries, 152 distinct target_pk, all corrected_field=is_test_record, all approved_by=forensic_audit_2026-07-19
- **B18**: SEQ=42(195)→43(198,+3 A8 artifacts)→44(195,-3 artifacts)→45(201,+6 new checks)
- **B19**: **OPEN GAP** — `last_run_results.json` is not chain-anchored; A8 Layer-1 reads it to classify cascade artifacts; tamper between runs would not be detected by chain. Remediation: include sha256(last_run_results.json) in chain entry (not implemented).
- **B20**: A4/A5/A15/A16/A18/A19/A20 DONE; A12/B5 OPEN; A10/A11/B15/B16/B17 not found in archived evidence files
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
