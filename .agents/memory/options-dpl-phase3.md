---
name: DPL Phase 3 — Reproducibility Replay & Institutional Audit Remediation
description: Directive 23 DONE. SEQ=132: 195P/8F. Freeze baseline maintained. seal_status field live in chain entries. 8 FAILs = known external blockers only.
---

## CURRENT STATE (post-Directive-23, SEQ=132)

**Chain head:** SEQ=132  
**SEQ=132 results:** 195 PASS, 8 FAIL, all=203  
**PSV SEQ=132:** 9 PASS / 0 FAIL (complete green)  
**refs.commit_sha:** == HEAD (A19 RESOLVED)  
**TREE=DIRTY expected** (Replit auto-commits at task end — A28 CERTIFICATION_GAP, not FAIL)

## Current file sha256 canonicals

| File | sha256 |
|---|---|
| `tools/verified_run.sh` | `2617d7bb4654228fd60bc3b971106cccb044f982043a29f14772dff54144bb29` |
| `artifacts/stock-scanner-api/verify_chain.sh` | `ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f` |
| `tools/verify_chain.sh` | `972ff44a02eded8816f97b8c1455211d1f224aa571459c4bc135835a68058d75` |
| `engine_integrity_refs.json` | `49c9adcf0299e17251ddc7d87c81258b940bffcb4c2e5c3dcb7c30de60e39b34` (after restore + PENDING annotation) |

## engine_integrity_refs.json seal state

**engine_root_hash:** `9698463b911555b138b2616fa4898d31ae25c7cb3fe55a0c10d0e70b6e0ee716`  
**verify_against_refs ok:** True  
**engine_root_hash_seal_basis:** `PENDING_INDEPENDENT_APPROVAL` — re-sealed 2026-07-25 to match commit a9c93ee (aiem_options_scheduler.py). Operator has seen the full diff but has NOT yet confirmed this as the authorized baseline. Until confirmed: re-seal is computationally correct, authorization is pending.

**What changed in a9c93ee (the re-sealed change):**
- Import of `aiem_pipeline_checkpoints as _chkp` at module-level with try/except
- 5 stage-checkpoint write calls: SEED_STAGE, P2_INIT, P2_GATE, P2_CAPTURE, DECISION_WRITTEN
- All wrapped in try/except — non-fatal to pipeline if checkpoint module unavailable
- No changes to scoring logic, weights, or `compute_req6_score`
- Session fc69526a (different from current audit session)

## 8 FAILs (SEQ=132)

| Check | Classification | Unblocks When |
|---|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER | Independent reviewer provides approved_by + approved_at |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER (approved_by sub-check) | Hash match now PASSES; approved_by still requires external reviewer identity in APPROVED_IDENTITIES |
| C49_db_role_gap_is_unmet_control | EXTERNAL_BLOCKER | Replit managed DB allows low-priv login-capable role |
| C49_ddl_privilege_gap_is_unmet_control | EXTERNAL_BLOCKER | Same DB role gap |
| C52B_scheduler_origin_decision_exists | PENDING | options-pipeline-scheduler next market day 09:45 ET |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE | Scheduler fires AND produces TRADE decision |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED | Blocked by C52B |
| C52C_historical_replay_eligible_row_exists | PENDING | First 09:45 ET scheduler run with replay capture |

## seal_status implementation (Directive 23 Item 5)

Added to `tools/verified_run.sh` (lines 65-86):
- Non-blocking pre-run `verify_against_refs()` call using engine_manifest
- Emits `WARNING: SEAL_STALE — ...` to stderr BEFORE eval "$CMD" runs
- Sets bash variable `_SEAL_STATUS` to SEAL_FRESH / SEAL_STALE / SEAL_UNKNOWN
- `seal_status` field added to chain entry JSON (included in entry_hash computation)

**SEAL_STALE test evidence:**
- SEQ=130: deliberately staled refs (DELIBERATELY_STALED_...) → cmd `echo SEAL_STALE_TEST_PAYLOAD` → chain entry `seal_status: SEAL_STALE` ✓ warning appeared in stderr before command output
- SEQ=131: restored hash → cmd `echo SEAL_FRESH_CONFIRM` → chain entry `seal_status: SEAL_FRESH` ✓
- SEQ=132: full verifier run → `seal_status: SEAL_FRESH` ✓

**Chain entry_hash payload schema v6 (SEQ=130+):** v5 fields + `seal_status`

## C43 fix (verify_discovery_cycle_fix.sh)

`CANONICAL_CHAIN_FILE="verified_run_chain.jsonl"` added at line 25 of
`artifacts/stock-scanner-api/tools/verify_discovery_cycle_fix.sh`.

## Item 1 tracking gap (explicitly recorded per Directive 23)

"Directed change" for session fc69526a (a9c93ee) ≠ "authorized to re-seal."
The prior agent session that added checkpoint writes to aiem_options_scheduler.py
had no mechanism to detect it was modifying a file inside the engine_root_hash set.
The seal was re-computed to be mathematically correct against the current source,
but operator authorization of that source as the new baseline is PENDING.
Do NOT read "directed, not unauthorized drift" as "authorized to re-seal."

## Chain state by entry (key entries)

| SEQ | status |
|---|---|
| 52-53 | LEGACY — no archive_sha256; permanently exempt |
| 54+ | archive_sha256 in entry dict |
| 130 | SEAL_STALE test (deliberately staled refs) |
| 131 | SEAL_FRESH confirmation |
| 132 | Full verifier run, 195/8, PSV 9/0 |

## verified_run.sh correct invocation pattern

`CMD="$1"` (single arg). Always pass as ONE quoted string with CWD baked in:
```
bash tools/verified_run.sh "cd artifacts/stock-scanner-api && python3 dpl/verify_dpl_phase3.py"
```

## Key structural decisions

**TREE=DIRTY is expected:** Replit auto-commits at task end. A28 is a CERTIFICATION_GAP (not FAIL).  
**APPROVED_IDENTITIES = empty set:** C28_approved_by sub-check fail-closed until external reviewer adds credentials.  
**C47B allowlist:** dpl/ *.py allowlist includes correction_ledger.py and scheduler_trace.py.

## Chain entry_hash payload schema

**v4 (SEQ≤53):** 13 fields (no archive_sha256)  
**v5 (SEQ=54–129):** v4 + `archive_sha256` in entry dict (excluded from hash payload — backward compatible)  
**v6 (SEQ≥130):** v5 + `seal_status` field (included in hash payload)

## Next unblock conditions

1. **Operator review of a9c93ee diff:** confirms scheduler checkpoint additions as authorized baseline → engine_root_hash_seal_basis changes to AUTHORIZED; C28_approved_by still requires external identity
2. **External reviewer with identity in APPROVED_IDENTITIES:** C48 + C28_approved_by unblock
3. **Next market day 09:45 ET:** options-pipeline-scheduler → C52B_scheduler_origin_decision_exists unblocks
4. **First TRADE decision day:** C52B_live_trade + C52C + C52C_historical unblock
