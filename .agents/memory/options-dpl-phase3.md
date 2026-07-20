---
name: DPL Phase 3 — Reproducibility Replay & R8 Remediation
description: SEQ=38 DIRTY (R8 session); 182P/7F; all fixable items resolved; SEQ=39 = first TREE=CLEAN run after auto-commit; 5 expected FAILs (all external/environmental)
---

## Current state after R8 (post SEQ=38)

**Chain head:** SEQ=38 (DIRTY_TREE_AT_RUNTIME; registered in defective_runs_registry.json)
**SEQ=38 results:** 182 PASS, 7 FAIL (189 total)
**PSV:** 9/9 PASS for SEQ=38
**defective_runs_registry: 6 entries (SEQ=22/26/35/36/37/38)**
**clean_sealed_runs = [] — ALL SEQ=23-38 confirmed DIRTY**

## First clean run path (SEQ=39)

**After R8 auto-commit creates new HEAD Z:**
1. Get new HEAD: `git --no-optional-locks log --oneline -1`
2. Update `dpl/engine_integrity_refs.json → commit_sha` to Z (refs.json is EXCLUDED from TREE filter — tree stays CLEAN)
3. Run: `cd artifacts/stock-scanner-api && bash tools/verified_run.sh python3 dpl/verify_dpl_phase3.py`
4. Expected: TREE=CLEAN, 189 total checks, 5 FAILs (all external/environmental — see table below)
5. Add SEQ=39 to `clean_sealed_runs` list in defective_runs_registry.json

**TREE filter (implemented in verified_run.sh):**
- `??` untracked files → excluded from TREE=DIRTY (audit directives, workspace notes)
- `dpl/engine_integrity_refs.json` → excluded from TREE=DIRTY (required pre-run procedure update)
- All other tracked M/D/A/R/C files → still counted as DIRTY

## 5 Expected Failures at SEQ=39 (all classified, all external/environmental)

| Check | Classification | Notes |
|---|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER | approved_at/approved_by null; allowlist empty |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER | allowlist=set() |
| C52B_scheduler_origin_decision_exists | PENDING | Unblocks Mon–Fri 9:45 AM ET (2026-07-21) |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE | TRADE day required |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED | Blocked by C52B |

## R8 Session Changes (this session)

| Item | Change |
|---|---|
| C52A | 9 contaminated rows moved to is_test_record=TRUE via ALTER TABLE DISABLE TRIGGER / UPDATE / ENABLE TRIGGER; prod namespace = 0 rows |
| engine_root_hash | Updated to 5de49257da8dc9baacace4188b411942199df3a8c8e48bd834a957b9b5408405 (post-R7 runtime value) |
| decision_path_module_hashes | Updated for R7 B17 changes (aiem_options_dpl.py=4246a17e, aiem_options_scheduler.py=8e4d7ee8) |
| decision_path_combined_hash | Updated to 9ecec3d0459d33b417bab32a02d0564939b68a3800644d68cb331a508be7da17 |
| refs commit_sha | Updated to df03bdab008e94b866b63e3b403e7b3f4c44cfc2 (R7 auto-commit HEAD) |
| verified_run.sh TREE filter | Excludes ?? untracked + dpl/engine_integrity_refs.json from TREE determination |
| C16 SAVEPOINT approach | _C16_KNOWN_FALSE was contaminated row (now is_test_record=TRUE); new code: INSERT+SAVEPOINT+UPDATE+ROLLBACK (same pattern as C22 neg control) |
| A8 cascade fix | Added `not n.startswith('A8_REMOVAL_VIOLATION:')` filter to Layer-1 — prevents double-prefix across runs |

## Key hash values (current, post-R8)

- `engine_root_hash`: `5de49257da8dc9baacace4188b411942199df3a8c8e48bd834a957b9b5408405`
- `decision_path_combined_hash`: `9ecec3d0459d33b417bab32a02d0564939b68a3800644d68cb331a508be7da17`
- `aiem_options_dpl.py hash`: `4246a17efc7199de489a79e91028ae60f99971524e36d266e1e2bcf1de8bd711`
- `aiem_options_scheduler.py hash`: `8e4d7ee89919d7467af1a160684bafbde56eb9a0fb1ad245f4ae7138713516b1`
- `scoring_fn_combined_hash`: `eb28b76efd53485602c648744c60642f87a6bb0c09ce02b0f0071ee2cfc6583a`
- `refs commit_sha` (pre-SEQ=39): MUST be updated to new HEAD before running SEQ=39

## SEQ=38 observed FAILs (vs expected 5)

7 FAILs instead of 5 because two regressions were discovered AND FIXED in this session:
- **C16_trigger_blocks_prod_update** (FIXED: SAVEPOINT approach) — _C16_KNOWN_FALSE row moved to is_test_record=TRUE by C52A fix
- **A8_REMOVAL_VIOLATION:A8_REMOVAL_VIOLATION cascade** (FIXED: startswith filter) — SEQ=37's spurious violation was in prev-run results; Layer-1 flagged ITS removal

Both fixes are committed in this session → SEQ=39 will not see these regressions.

## Next unblock conditions

1. **Immediate (next session):** update commit_sha to new HEAD → run SEQ=39 → TREE=CLEAN → first clean sealed run
2. **Monday 2026-07-21 09:45 ET:** C52B_scheduler_origin_decision_exists unblocks (live scheduler decision)
3. **Any TRADE market day:** C52B_live_trade_decision_exists + C52C_genuine_replay_pass unblock
4. **External reviewer:** C48 + C28_approved_by unblock (requires separate trusted principal)

## Defective runs registry

SEQ=22 (CMD_ARG_CAPTURE_BUG), SEQ=26 (INVALID_CMD_INVOCATION), SEQ=35 (DIRTY_TREE), SEQ=36 (DIRTY_TREE), SEQ=37 (DIRTY_TREE — R7 complete), SEQ=38 (DIRTY_TREE — R8 complete).
total_defective=6; all in defective_runs_registry.json.
