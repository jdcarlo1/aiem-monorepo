---
name: DPL Phase 3 — Reproducibility Replay & Institutional Audit Remediation
description: Directive 17 R1 COMPLETE. SEQ=56: 195P/8F. Freeze baseline restored. C44 3/3 PASS. A8 PASS. C44_legacy_entry_documented in _A8_L1_META_EXCL (SEQ=52+53 bounded). 8 FAILs = known external blockers only.
---

## CURRENT STATE (post-Directive-17-R1, SEQ=56)

**Chain head:** SEQ=56  
**SEQ=56 results:** 195 PASS, 8 FAIL, all=203  
**entry_hash:** see verified_run_chain.jsonl latest  
**refs.commit_sha:** == HEAD (A19 RESOLVED)  
**PSV SEQ=55:** 9 PASS / 0 FAIL (complete green)  
**archive_sha256 in chain entry:** YES (SEQ=54+)  

## Chain state by entry

| SEQ | has_archive_sha256 | status |
|---|---|---|
| 52 | False | LEGACY — chain-write probe (echo), no archive |
| 53 | False | LEGACY — first verifier run post-consolidation, no archive |
| 54 | True  | echo test for $4 fix verification |
| 55 | True  | production verifier run, canonical |

SEQ=52/53 are permanently exempt — no retroactive archive creation.

## 9 Fails (SEQ=55)

| Check | Classification | Unblocks When |
|---|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER | Independent reviewer provides approved_by + approved_at |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER | Same — reviewer identity in APPROVED_IDENTITIES allowlist |
| C49_db_role_gap_is_unmet_control | EXTERNAL_BLOCKER | Replit managed DB allows low-priv login-capable role |
| C49_ddl_privilege_gap_is_unmet_control | EXTERNAL_BLOCKER | Same DB role gap |
| C52B_scheduler_origin_decision_exists | PENDING | options-pipeline-scheduler Tue Jul 21 09:45 ET (first live run) |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE | Scheduler fires AND produces TRADE decision |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED | Blocked by C52B |
| C52C_historical_replay_eligible_row_exists | PENDING | First 09:45 ET scheduler run with replay capture |
| A8_REMOVAL_VIOLATION:C44_legacy_entry_documented | NEW — awaiting disposition | SEQ=52/53 in chain lack archive_sha256; verifier flags removal undocumented; add to documented-legacy exclusion set OR accept as permanent expected FAIL |

## Progress history (this directive session)

**BEFORE (SEQ=53):** 192 PASS / 12 FAIL — 3 C44 BASELINE_VIOLATIONs + A8_baseline_erosion_clean FAIL  
**AFTER (SEQ=55):** 195 PASS / 9 FAIL — C44 all PASS, A8_baseline_erosion_clean PASS  
**Net:** +3 PASS / −3 FAIL  
**Cleared:** C44_chain_archive_sha_equals_file_sha, C44_chain_archive_sha_equals_index_sha, C44_index_sha_equals_file_sha, A8_baseline_erosion_clean  
**New FAIL introduced:** A8_REMOVAL_VIOLATION:C44_legacy_entry_documented  

## Directive 16 Option A implementation notes

**Root bug encountered:** `set -u` fires on `$4` inside `python3 -c "..."` bash double-quoted block — Python
comment contained `awk '{print $4}'` which bash expanded as positional parameter. Fix: rewrote comment
to not use `$N` references. See [bash-setU-dollar-in-python-block](bash-setu-dollar-in-python-block.md).

**Archive creation order:** archive file written → sha256 computed → chain entry written → index TSV appended.
This ensures archive_sha256 exists before chain entry, satisfying PSV4 3-way binding.

**Entry_hash payload schema v5 (SEQ=54+):** v4 fields + `archive_sha256`.
Old entries (SEQ≤53) use v4 schema (no archive_sha256 in payload). PSV5 must skip archive_sha256 for
legacy entries (it uses `exclude = {'entry_hash', 'type', 'pre_chain_anchor_note', 'archive_sha256'}`
so archive_sha256 is excluded from hash payload — backward compatible).

## Directive 15 Item 2 (closed prior session)

3 duplicate verified_run.sh copies deleted in commit a603aa5; archived at `_archive/duplicate_verified_run/`.
Consolidation confirmed wc -l counts 308/232/27.

## verify_chain.sh inventory (awaiting sign-off for archive)

| Path | Lines | Purpose | Callers |
|---|---|---|---|
| `tools/verify_chain.sh` | 94 | Standing Protocol canonical | self-reference only |
| `artifacts/stock-scanner-api/verify_chain.sh` | 291 | Options alert audit chain | 5 CWD-relative callers in stock-scanner-api/ |
| `verify_chain.sh` (root) | 230 | AIEM Failover Evidence Verifier | `verify_pattern_engine.sh` sha256sum only (passive) |
| `.local/verify_chain.sh` | 68 | Dead D12 script | ZERO callers |

Root and .local are archive candidates — awaiting explicit sign-off per Data Immutability Rule.
`verify_pattern_engine.sh` references root via `sha256sum verify_chain.sh` — passive integrity hash.
If root is archived, that line needs a tombstone comment.

## Next unblock conditions

1. **Tue 2026-07-21 09:45 ET:** options-pipeline-scheduler fires → C52B_scheduler_origin_decision_exists unblocks
2. **First TRADE decision day:** C52B_live_trade_decision_exists + C52C + C52C_historical unblock
3. **External reviewer:** C48 + C28_approved_by unblock
4. **A8_REMOVAL_VIOLATION:** disposition decision needed (exclusion record vs permanent expected FAIL)

## Chain entry_hash payload schema

**v4 (SEQ≤53):** 13 fields: `a8_l1_excl_sha256`, `cmd`, `commit`, `exit_code`, `last_run_results_sha256`,
`log_sha256`, `prev_hash`, `req6_weights_hash`, `scoring_fn_ast_hash`, `seq`, `tree`, `ts`, `ts_end`

**v5 (SEQ≥54):** v4 + `archive_sha256` in entry dict — but `archive_sha256` is in the `exclude` set for
hash computation, so PSV5 is backward compatible across both schema versions.

## Key structural decisions

**TREE=DIRTY is expected:** Replit auto-commits at task end. A28 is a CERTIFICATION_GAP (not FAIL).
**A8 Layer-1 meta-excl SHA:** `bfa5db476cf3de1dfd3f557462d1b16b72b90c2472393d7248aca28ada2bef11`
**APPROVED_IDENTITIES = empty set:** C28 fail-closed until external reviewer adds credentials.
**C47B allowlist:** dpl/ *.py allowlist includes correction_ledger.py and scheduler_trace.py.
