---
name: DPL Phase 3 — Reproducibility Replay & Institutional Audit Remediation
description: SEQ=39 183P/6F DIRTY; all fixable issues resolved; SEQ=40=first TREE=CLEAN (5 FAILs all external); institutional audit evidence package written
---

## Current state (post institutional audit directive)

**Chain head:** SEQ=39 (DIRTY_TREE_AT_RUNTIME; registered in defective_runs_registry.json)
**SEQ=39 results:** 183 PASS, 6 FAIL (189 total)
**total_defective = 7** (SEQ=22/26/35/36/37/38/39)
**clean_sealed_runs = [] — ALL SEQ=23-39 confirmed DIRTY**

## First clean run path (SEQ=40)

**After this session's auto-commit creates new HEAD Z:**
1. Get new HEAD: `git --no-optional-locks log --oneline -1`
2. Update `dpl/engine_integrity_refs.json → commit_sha` to Z (refs.json EXCLUDED from TREE filter)
3. Run: `cd artifacts/stock-scanner-api && bash tools/verified_run.sh python3 dpl/verify_dpl_phase3.py`
4. Expected: TREE=CLEAN, 189 total checks, 5 FAILs (all external/environmental)
5. Add SEQ=40 to `clean_sealed_runs` list in defective_runs_registry.json

**TREE filter (3 exclusions as of this session):**
- `??` untracked files → excluded
- `dpl/engine_integrity_refs.json` → excluded (required pre-run procedure update)
- `tools/verified_run_seq` → excluded (monotonic runtime counter, mutated every run; integrity enforced by chain)

## 5 Expected Failures at SEQ=40 (all external/environmental)

| Check | Classification | Unblocks When |
|---|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER | Independent reviewer provides approved_by + approved_at |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER | Same — reviewer identity in allowlist |
| C52B_scheduler_origin_decision_exists | PENDING | options-pipeline-scheduler Mon–Fri 09:45 AM ET |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE | Scheduler fires AND produces TRADE decision |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED | Blocked by C52B |

## Session Changes (institutional audit directive response)

| Item | Change |
|---|---|
| refs.json commit_sha | Updated to 9a1af0d9c3973d53 (session-summary auto-commit HEAD) |
| A8 cascade fix (v2) | Separate `_a8_cascade_arts` BEFORE registry lookup; no KeyError in else-branch; SEQ=39 had A8_enforcement_error from KeyError on `A8_REMOVAL_VIOLATION:A8_baseline_erosion_clean` |
| verified_run_seq TREE exclusion | Added `grep -v 'tools/verified_run_seq'` to TREE filter; justified as runtime counter whose integrity is enforced by chain |
| SEQ=39 registered | DIRTY_TREE (183P/6F); 7 total defective |
| Institutional evidence package | R8_Institutional_Audit_Evidence.md — 11-item response with real SQL, git, SHA-256, chain, trigger status, DB counts |

## Key findings from institutional audit

- **Trigger status**: All 4 triggers ENABLED (tgenabled='O', confirmed by pg_trigger catalog)
- **Engine root hash**: engine_manifest.verify_against_refs() → ok=True; live=approved=5de49257...
- **C52A**: prod namespace = 0 rows; 6 pre-wiring audit rows documented (created 01:17-01:57 vs first replay at 04:28 on 2026-07-19)
- **A8 cascade root cause**: `_a8_removed` (before cascade separation) included `A8_REMOVAL_VIOLATION:A8_baseline_erosion_clean`; `_a8_viol` was empty but else-branch did `_A8_SUPERSEDE_REGISTRY[rn]` → KeyError → caught as `A8_enforcement_error`
- **verified_run_seq root cause**: runtime counter file tracked in git; mutated at run start by flock+increment; should have been excluded from TREE filter alongside refs.json

## Key hash values (current)

- `engine_root_hash`: `5de49257da8dc9baacace4188b411942199df3a8c8e48bd834a957b9b5408405`
- `decision_path_combined_hash`: `9ecec3d0459d33b417bab32a02d0564939b68a3800644d68cb331a508be7da17`
- `scoring_fn_combined_hash`: `eb28b76efd53485602c648744c60642f87a6bb0c09ce02b0f0071ee2cfc6583a`
- `refs commit_sha` (pre-SEQ=40): MUST be updated to new HEAD before running SEQ=40

## Chain state

- 25 entries: SEQ=0(GENESIS), SEQ=15-39 (all RUN)
- All 25 entry_hashes recompute correctly (C33 PASS at SEQ=39)
- Chain head (SEQ=39): `36ac5373ab3bb0ea7b6a917077ec3aa7bf754a2216f443ad65532ee8f8cd745f`

## Defective runs registry

SEQ=22(CMD_ARG), 26(INVALID_CMD), 35(DIRTY), 36(DIRTY), 37(DIRTY_R7), 38(DIRTY_R8), 39(DIRTY_R8-audit)
total_defective=7; all in defective_runs_registry.json

## Next unblock conditions

1. **Immediate (next session):** update commit_sha to new HEAD → run SEQ=40 → TREE=CLEAN → first clean sealed run
2. **Monday 2026-07-21 09:45 ET:** C52B_scheduler_origin_decision_exists unblocks
3. **Any TRADE market day:** C52B_live_trade_decision_exists + C52C unblock
4. **External reviewer:** C48 + C28_approved_by unblock
