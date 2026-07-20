---
name: DPL Phase 3 — Reproducibility Replay & R7 Remediation
description: SEQ=37 DIRTY (all R7 A14-A20 + B15-B17 code complete); SEQ=38 clean after auto-commit; 7 FAILs expected; A8 Layer-1 meta-excl fix applied
---

## Current state after R7 (post SEQ=37)

**Chain head:** SEQ=37 (DIRTY_TREE_AT_RUNTIME; registered in defective_runs_registry.json)
**SEQ=37 results:** 181 PASS, 8 FAIL (189 total) — 8th FAIL was spurious A8_REMOVAL_VIOLATION ordering bug (now fixed)
**PSV:** 9/9 PASS for SEQ=37
**All R7 blocking items A14-A20 implemented. All non-blocking B15-B17 implemented.**
**clean_sealed_runs = [] — ALL SEQ=23-37 confirmed DIRTY**

## First clean run path (SEQ=38)

**After R7 auto-commit creates new HEAD Z:**
1. Check new HEAD: `git --no-optional-locks log --oneline -1`
2. Update `dpl/engine_integrity_refs.json → commit_sha` to Z
3. Run: `cd artifacts/stock-scanner-api && bash tools/verified_run.sh python3 dpl/verify_dpl_phase3.py`
4. Expected: TREE=CLEAN, 188 total checks, 7 FAILs (pre-existing only — see table below)

**Note:** C28_refs_commit_sha_matches_run_head will FAIL at SEQ=38 unless step 2 is done first.
Without step 2: 188 total, 8 FAILs (7 pre-existing + C28_refs_commit_sha_matches_run_head).
With step 2: 188 total, 7 FAILs (same pre-existing list as before). Preferred.

## 7 Expected Failures at SEQ=38 (all classified)

| Check | Classification | Notes |
|---|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER | approved_at/approved_by null; allowlist empty |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER | allowlist=set() |
| C52A_verifier_fixtures_contaminate_prod_namespace | IMPLEMENTATION_DEFECT | 9 rows in contamination_registry.json |
| C52B_scheduler_origin_decision_exists | PENDING | Unblocks Mon–Fri 9:45 AM ET |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE | TRADE day required |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED | Blocked by C52B |
| C28_live_engine_root_hash_matches_approved | EXTERNAL_BLOCKER | engine hash ≠ approved hash |

## R7 Blocking Items (A14-A20) — all complete

| Item | Change |
|---|---|
| A14 | SEQ=36 registered in defective_runs_registry.json as DIRTY_TREE_AT_RUNTIME |
| A15 | clean_sealed_runs=[] with evidence table in defective_runs_registry.json |
| A16 | C50_clean_sealed_runs_all_verified_clean (reads TREE= from archives) + C50_neg_control_dirty_seq_detected_as_dirty; supersedes C50_clean_runs_include_23_and_24 |
| A17.3 | All 9 _A8_SUPERSEDE_REGISTRY entries carry rationale + subsumption proof comments |
| A18 | C48_neg_self_approval_is_forbidden + C48_approval_metadata_only_flag_set restored as separate checks; removed from supersede registry |
| A19 | C28_refs_commit_sha_matches_run_head added (new check); CERTIFICATION_GAP_A19 emitted; engine_integrity_refs.json commit_sha=7000bc79 |
| A20 | CERTIFICATION_GAP_C49 emitted at certification level (not buried in C49 check) |

## R7 Non-Blocking Items (B15-B17) — all complete

| Item | Change |
|---|---|
| B15 | DDL commit f7581e6 at 2026-07-20T00:55:49Z confirmed 8h52m after 16:04 row; N1 ALTER TABLE explanation in chain |
| B16 | NOT_EXECUTED label on verify_chain.sh sha256 line in verified_run.sh sealed header |
| B17 | `get_contamination_exclusions()` added to aiem_options_dpl.py; scheduler calls it at DPL bootstrap startup |

## A8 Layer-1 Meta-Exclusion Fix (discovered at SEQ=37)

**Problem:** A8 Layer-1 runs before A8 Layer-2. Layer-2 adds `A8_baseline_erosion_clean` to `_PASS`.
At Layer-1 evaluation time, `A8_baseline_erosion_clean` is not yet in `_PASS` or `_FAIL` (Layer-2 hasn't run).
Layer-1 saw it in SEQ=36's results but not in current `_a8_curr` → spurious `A8_REMOVAL_VIOLATION:A8_baseline_erosion_clean`.

**Fix:** `_A8_L1_META_EXCL = {'A8_baseline_erosion_clean', 'A8_baseline_file_missing'}` added to Layer-1.
These names are excluded from `_a8_removed` before violation check.
After fix: SEQ=38 will have 188 checks (not 189 with the false violation).

## Defective runs registry

SEQ=22 (CMD_ARG_CAPTURE_BUG), SEQ=26 (INVALID_CMD_INVOCATION), SEQ=35 (DIRTY_TREE), SEQ=36 (DIRTY_TREE), SEQ=37 (DIRTY_TREE — R7 workflow constraint, all code complete).

## Key hash values

- `scoring_fn_combined_hash` in refs: `eb28b76efd53485602c648744c60642f87a6bb0c09ce02b0f0071ee2cfc6583a`
- `engine_root_hash` in refs: `4ff60253f52e37d5b1b65dbae40c56f960a835b59bab78714036b9dabb55f4b4`
- `commit_sha` in refs: `7000bc7909a6bd0763a3f78976591d602521f6d9` (update to new HEAD before SEQ=38)

## Next unblock condition

1. **Immediate (next session):** update commit_sha in engine_integrity_refs.json to new HEAD → run SEQ=38 → first CLEAN run
2. **Monday 2026-07-21 09:45 ET:** C52B_scheduler_origin_decision_exists unblocks
3. **Any TRADE market day:** C52B_live_trade_decision_exists + C52C_genuine_replay_pass unblock
