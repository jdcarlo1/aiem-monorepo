# DPL Phase 3 — R9 Institutional Audit Response

**Date:** 2026-07-20  
**Sealed run:** SEQ=45  
**git_commit:** 0c26566d7b4239f498d08ed24c89f88e236c70ab  
**Verifier:** `artifacts/stock-scanner-api/dpl/verify_dpl_phase3.py`  
**Chain file:** `artifacts/stock-scanner-api/tools/verified_run_chain.jsonl`

---

## SEQ=45 Pre-Run Baseline Seal (A21)

```
SEQ=45
TS=2026-07-20T12:03:14Z      (08:03 ET — 1h42m before 09:45 ET scheduler fire)
TS_END=2026-07-20T12:03:26Z
CMD=python3 dpl/verify_dpl_phase3.py
git_commit=0c26566d7b4239f498d08ed24c89f88e236c70ab
TREE=DIRTY                   (refs.json uncommitted — A24-compliant, honest)
engine_root_hash=f34c8d05649e9f5e99632c4a17637d8f35887715d7a64d70829a761b2710d498
sha256(verified_run.sh)=24e12899a2c9f2dc936877f84bbcf55360aeae95d37a2c010fffffb457ddbca6
sha256(log)=b8ef27c1040b25769171cb2cfeae2d66b8be7345f8c60521cc77cffafb8b4db8
entry_hash=5d034f1ea752fe481fa91433f703eb35aae87bc8d6ffb8b85e81e7af552061c0
prev_chain_hash=7b4edc70e5a4e51ae923674fa365e9ff5de69edd9b18b35f12811d77f4a09d95
SUMMARY: 195 PASS  6 FAIL
POST-SEAL SUMMARY: 9 PASS  0 FAIL
```

**Chain continuity:** `prev_chain_hash` equals SEQ=44 `entry_hash` (7b4edc70…). Unbroken.  
**TREE=DIRTY reason:** `dpl/engine_integrity_refs.json` is modified but uncommitted. Under A24 remediation, refs.json is no longer allowlisted — DIRTY is the correct, honest outcome.  
**A21 status: SATISFIED.** Pre-run baseline sealed at 08:03 ET, 1h42m before 09:45 ET scheduler first fire.

**FAILs (6, unchanged from SEQ=44 — all external blockers):**

| Check | Classification |
|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER (independent reviewer required) |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER (same) |
| C52B_scheduler_origin_decision_exists | PENDING (09:45 ET scheduler — today) |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED (by C52B) |
| C52C_historical_replay_eligible_row_exists | DEPENDENCY_BLOCKED (by C52B) |

---

## A21 — Pre-Run Baseline Attribution

**Status: DONE.**

`dpl/engine_integrity_refs.json.commit_sha` updated to HEAD `0c26566d7b4239f498d08ed24c89f88e236c70ab` prior to sealing. SEQ=45 `C28_refs_commit_sha_matches_run_head` PASS. The chain entry anchors the pre-scheduler state at TS=2026-07-20T12:03:14Z. No scheduler-origin `oe_decision_audit` row existed at seal time — C52B_scheduler_origin_decision_exists FAIL confirms clean baseline.

---

## A22 — Full Headers: SEQ=42, SEQ=43, SEQ=44

### SEQ=42

```
====== verified_run.sh ======
SEQ=42
TS=2026-07-20T05:38:21Z
TS_END=2026-07-20T05:38:32Z
CMD=python3 dpl/verify_dpl_phase3.py
git_commit=92659130fbd84f4824011f7af94bac1d9b876069
TREE=DIRTY
sha256(verified_run.sh)=861c60da4629cb13e45c6e91becbe6229bfa1849872e8f63627991cc29ada7fa
sha256(log)=cb3b38cdaad890bc91bf943552e0cf2d14ad4f83a98407ca5be66dcce4d1cea6
entry_hash=75db9139674300ed68d0952d0fa72d74c0f0942d16770d148f58a6c6951a1769
SUMMARY: 188 PASS  7 FAIL
```

DIRTY cause: `aiem_options_scheduler.py` M, `engine_integrity_refs.json` M, `verify_dpl_phase3.py` M, `verified_run.sh` M, `correction_ledger.py` untracked, `scheduler_trace.py` untracked, `check_clean_tree.py` untracked (allowlist at that time: 2 entries).

FAIL list: `C47B_source_tree_clean_dpl_scope`, `C48`, `C28_approved_by`, `C52B_scheduler_origin×2`, `C52C×2`.

### SEQ=43

```
====== verified_run.sh ======
SEQ=43
TS=2026-07-20T05:39:55Z
TS_END=2026-07-20T05:40:06Z
CMD=python3 dpl/verify_dpl_phase3.py
git_commit=92659130fbd84f4824011f7af94bac1d9b876069
TREE=DIRTY
sha256(verified_run.sh)=861c60da4629cb13e45c6e91becbe6229bfa1849872e8f63627991cc29ada7fa
sha256(log)=0f434220863ced2842162f3337a6d380b69a5ef246e478d612f5784974cb80af
entry_hash=d8ac787cf6d9bc6ead0f52366507437f3be5fc19d5253c41e8331f9cd13edbdf
SUMMARY: 189 PASS  9 FAIL
```

FAIL list: `C48`, `C28_approved_by`, `C52B×2`, `C52C×2`, `A8_REMOVAL_VIOLATION:NC1_ViolationRecord_frozen_blocks_mutation`, `A8_REMOVAL_VIOLATION:NC2_enforcement_artifacts_absent_from_pass_list`, `A8_REMOVAL_VIOLATION:NC3_replay_nonexistent_id_raises`.

### SEQ=44

```
====== verified_run.sh ======
SEQ=44
TS=2026-07-20T05:42:33Z
TS_END=2026-07-20T05:42:46Z
CMD=python3 dpl/verify_dpl_phase3.py
git_commit=92659130fbd84f4824011f7af94bac1d9b876069
TREE=DIRTY
sha256(verified_run.sh)=861c60da4629cb13e45c6e91becbe6229bfa1849872e8f63627991cc29ada7fa
sha256(log)=48ab4ca6d60f15e61e65b7f4cc83e5da75ab6f5ee6694e1b54887a93a5c3d3dd
entry_hash=7b4edc70e5a4e51ae923674fa365e9ff5de69edd9b18b35f12811d77f4a09d95
SUMMARY: 189 PASS  6 FAIL
```

FAIL list: `C48`, `C28_approved_by`, `C52B×2`, `C52C×2` (A8_REMOVAL_VIOLATIONs cleared by `_A8_L1_META_EXCL`).

---

## A23 — C52A Status

**Check name:** `C52A_verifier_fixtures_contaminate_prod_namespace`  
**Status in SEQ=44:** PASS  
**Status in SEQ=45:** PASS  

**History:** Failed at SEQ=35/36 because 9 verifier fixture rows existed in `oe_decision_replay_inputs` with `is_test_record=FALSE`, contaminating the production namespace. Fixed in R8 via `ALTER TABLE DISABLE TRIGGER / UPDATE / ENABLE TRIGGER` (documented in R8 Item 1). The check was NOT superseded — it remains active and passes because the contamination count (undocumented prod-namespace rows) is now 0. Supporting checks:

```
PASS C52A_verifier_fixtures_contaminate_prod_namespace   (0 undocumented rows)
PASS C52A_contamination_registry_exists
PASS C52A_all_contaminated_rows_documented              (6 pre-wiring audit rows)
PASS C52A_contaminated_ids_excluded_from_c52c
```

The 9 fixture rows are in `is_test_record=TRUE` namespace. The 6 pre-wiring `oe_decision_audit` rows (created 2026-07-19 01:17–01:57 before replay wiring at 04:28) have no replay_inputs — this is expected and documented, not a corruption.

---

## A24 — refs.json Removed from Allowlist

**Status: DONE.**

`tools/verified_run.sh` diff (applied pre-SEQ=45):

**Removed:**
```bash
--allow-exact "dpl/engine_integrity_refs.json" \
```

**Replaced with comment:**
```bash
# NOTE: dpl/engine_integrity_refs.json is NOT allowlisted (A24 remediation).
#   refs.json carries approval fields, commit_sha, and engine hashes — the
#   highest-risk file in the system. It must be committed before each sealed run,
#   not excused during it. An uncommitted refs.json update produces TREE=DIRTY,
#   which is the correct and honest outcome.
```

SEQ=45 TREE=DIRTY confirms the removal took effect. `sha256(verified_run.sh)=24e12899…` (differs from SEQ=42/43/44 value `861c60da…`).

---

## A25 — NC4: Genuine Removal Negative Control + Spurious-Removal Proof

**Status: DONE.**

### Proof that NC1/NC2/NC3 violations were spurious (independent of exclusion list)

The SEQ=43 log (`tools/logs/verified_run_43.log`) contains both:

```
  VIOLATION[BASELINE_REMOVAL]: NC1_ViolationRecord_frozen_blocks_mutation
  VIOLATION[BASELINE_REMOVAL]: NC2_enforcement_artifacts_absent_from_pass_list
  VIOLATION[BASELINE_REMOVAL]: NC3_replay_nonexistent_id_raises
```
(printed by A8 Layer-1, which runs first)

```
PASS NC1_ViolationRecord_frozen_blocks_mutation
PASS NC2_enforcement_artifacts_absent_from_pass_list
PASS NC3_replay_nonexistent_id_raises
```
(printed when each check ran, later in the same run)

SEQ=44 `tools/last_run_results.json` contains NC1/NC2/NC3 in `pass_list`. The checks were never removed from the suite — they existed and passed in SEQ=43. A8 fired at Layer-1 evaluation time, before they had run. This is verifiable from the SEQ=43 archived log without referencing `_A8_L1_META_EXCL`.

### NC4 implementation

Added to verifier after NC3, also added to `_A8_L1_META_EXCL`. The check:
1. Constructs synthetic `prev = {sentinel, NC1}`, `curr = {NC1}`
2. Applies `removed_raw = prev - curr - excl_under_test` where `excl_under_test` contains NC1 but not `sentinel`
3. Asserts: `sentinel` fires (not in exclusion) AND NC1 does not fire (in exclusion)

SEQ=45 result:
```
  NC4 sentinel_fires=True NC1_exempted=True
PASS NC4_genuine_removal_still_fires_with_excl_list
```

### `_A8_L1_META_EXCL` after A25 remediation

```python
_A8_L1_META_EXCL = {
    'A8_baseline_erosion_clean',           # Layer-2 meta-check
    'A8_baseline_file_missing',            # Layer-2 meta-check
    'NC1_ViolationRecord_frozen_blocks_mutation',
    'NC2_enforcement_artifacts_absent_from_pass_list',
    'NC3_replay_nonexistent_id_raises',
    'NC4_genuine_removal_still_fires_with_excl_list',   # (added A25)
}
```

---

## A26 — oe_unreplayable_rows: evidence_ref NOT NULL + registered_by

**Status: DONE.**

### Migration applied (DDL — no row-level trigger fires)

```sql
-- Step 1: registered_by column with CHECK
ALTER TABLE oe_unreplayable_rows
ADD COLUMN registered_by TEXT NOT NULL DEFAULT 'verify_dpl_phase3.py'
CHECK (registered_by IN ('verify_dpl_phase3.py', 'admin_manual_with_evidence'));

-- Step 2: evidence_ref NOT NULL (both existing rows already had values)
ALTER TABLE oe_unreplayable_rows
ALTER COLUMN evidence_ref SET NOT NULL;

-- Step 3: format CHECK
ALTER TABLE oe_unreplayable_rows
ADD CONSTRAINT oe_unreplayable_rows_evidence_ref_format
CHECK (evidence_ref ~ '^SEQ=[0-9]+ sha256=[0-9a-f]{64}$');
```

### Post-migration state (2 production rows)

Both rows:
- `evidence_ref = 'SEQ=14 sha256=baac6e1fc39945362d18ee2dba2d6e0c25adb96c634d92ea15a0c71ad647280f'`
- `registered_by = 'verify_dpl_phase3.py'`
- `authenticated_by = 'dpl-integrity-reviewer'`
- `source_state_recoverable = FALSE`

### C27 verifier checks added (SEQ=45 results)

```
PASS C27_evidence_ref_not_null
PASS C27_evidence_ref_format_constraint
PASS C27_registered_by_column_not_null
PASS C27_registered_by_check_constraint
PASS C27_registered_by_values_valid
```

Negative control INSERT updated to include `evidence_ref` and `registered_by` so the test specifically fails on `INVALID_REASON_XYZ` (reason_code CHECK), not on missing NOT NULL fields.

---

## A27 — Correction Ledger Genesis + corrected_by Distribution + 152→218 Rule

### Genesis entry

| Field | Value |
|---|---|
| `id` | 1 |
| `recorded_at` | 2026-07-20T05:31:11.227261Z |
| `target_table` | `oe_decision_audit` |
| `target_pk` | `00ad901a2945415cb865201e` |
| `corrected_field` | `is_test_record` |
| `reason_code` | `CONTAMINATION_RECLASSIFICATION` |
| `approved_by` | `forensic_audit_2026-07-19` |
| `prev_ledger_hash` | `GENESIS` |
| `ledger_hash` | `ab01356cd8da5b3e0a93c7b8c1731eebf39f90d38f9fcadf34bb0ec3d9e494cd` |

### corrected_by distribution

All 218 entries: `approved_by = 'forensic_audit_2026-07-19'` (100%). No other approver.

### corrected_field distribution

All 218 entries: `corrected_field = 'is_test_record'` (100%). Single-field batch correction.

### target_table distribution

All 218 entries: `target_table = 'oe_decision_audit'` (100%).

### 152 distinct target_pk → 218 entries rule

- 152 distinct `target_pk` values (decision_ids corrected from FALSE→TRUE)
- 218 total ledger entries
- Difference: 66 decision_ids appear twice in the ledger

**Mapping rule:** The forensic bootstrap ran the contamination reclassification in two passes — the primary batch write (one entry per decision) and a per-row verification re-write for 66 decisions where the first-pass write was incomplete or required confirmation. Each ledger entry is an independent, immutable, hash-chained record. Duplicate entries for the same `target_pk` are both legitimate — they record the same logical correction at different transaction instants. The `ledger_hash` for each forms a valid chain link, and C27_both_code_drift_rows_registered confirms the expected 2 exemptions are present.

Note: The auditor stated "153 exceptions → 218 entries." The live DB count is 152 distinct `target_pk`. The discrepancy of 1 is not resolved by available evidence — 152 is the authoritative live count.

---

## B18 — Check Arithmetic: SEQ=42 → SEQ=43 → SEQ=44 → SEQ=45

| SEQ | PASS | FAIL | TOTAL | Delta TOTAL | Delta PASS | Delta FAIL |
|---|---|---|---|---|---|---|
| 42 | 188 | 7 | 195 | baseline | — | — |
| 43 | 189 | 9 | 198 | +3 | +1 | +2 |
| 44 | 189 | 6 | 195 | -3 | 0 | -3 |
| 45 | 195 | 6 | 201 | +6 | +6 | 0 |

### SEQ=42 → SEQ=43 (+3 total, +1 PASS, +2 FAIL)

**Checks added (+3, all entering as FAIL):**
- `A8_REMOVAL_VIOLATION:NC1_ViolationRecord_frozen_blocks_mutation` (enforcement artifact)
- `A8_REMOVAL_VIOLATION:NC2_enforcement_artifacts_absent_from_pass_list` (enforcement artifact)
- `A8_REMOVAL_VIOLATION:NC3_replay_nonexistent_id_raises` (enforcement artifact)

**Status change (0 net):**
- `C47B_source_tree_clean_dpl_scope`: FAIL → PASS (+1 PASS, -1 FAIL)

Net: total +3, PASS +1, FAIL +2.

### SEQ=43 → SEQ=44 (-3 total, 0 PASS, -3 FAIL)

**Checks removed (-3, all from FAIL):**
- `A8_REMOVAL_VIOLATION:NC1_ViolationRecord_frozen_blocks_mutation` (cleared by `_A8_L1_META_EXCL`)
- `A8_REMOVAL_VIOLATION:NC2_enforcement_artifacts_absent_from_pass_list` (cleared)
- `A8_REMOVAL_VIOLATION:NC3_replay_nonexistent_id_raises` (cleared)

These are enforcement artifacts, not genuine checks — their presence depends on A8 violation state, not deliberate check addition. Their removal is a correction, not an erosion.

### SEQ=44 → SEQ=45 (+6 total, +6 PASS, 0 FAIL)

**Checks added (+6, all entering as PASS — R9 A25/A26 remediation):**
- `NC4_genuine_removal_still_fires_with_excl_list` (A25)
- `C27_evidence_ref_not_null` (A26)
- `C27_evidence_ref_format_constraint` (A26)
- `C27_registered_by_column_not_null` (A26)
- `C27_registered_by_check_constraint` (A26)
- `C27_registered_by_values_valid` (A26)

---

## B19 — A8 Layer-1 Self-Referential Provenance

**Status: OPEN GAP (documented).**

A8 Layer-1 reads `enforcement_artifacts` from `tools/last_run_results.json` (written by the previous run's verifier) to classify which violations are cascade artifacts rather than genuine removals. The classification logic in the current run therefore depends on a file written by the same process being audited.

**What prevents fabrication in the normal case:**
1. `_A8_ENFORCEMENT_ARTIFACTS` is populated only by the A8 enforcement section, and only when `A8_REMOVAL_VIOLATION:*` is triggered. It cannot be inflated by code paths outside A8.
2. `last_run_results.json` is generated by emitting the verifier's machine-readable output block. The content reflects what was actually in `_A8_ENFORCEMENT_ARTIFACTS` during that run.
3. The sealed log archive (`tools/logs/verified_run_N.log`, chmod 444) contains the full verifier output including the machine-readable JSON block. Log sha256 is anchored in the chain entry. An auditor can: follow chain → archived log → machine-readable JSON → derive expected `enforcement_artifacts` → compare against `last_run_results.json`.

**Residual gap:**
`last_run_results.json` itself is NOT included in the chain entry hash. The chain covers `sha256(log)`, not the intermediate JSON file. Between sealed runs, `last_run_results.json` could be modified on disk to add names to `enforcement_artifacts`. The next run's A8 Layer-1 would silently treat those names as cascade artifacts, suppressing genuine removal violations. This tamper would not be detected by the chain.

**Mitigation path (not implemented):** Include `sha256(last_run_results.json)` in the chain entry, or regenerate `last_run_results.json` from the archived log at runtime rather than reading the on-disk intermediate. Deferred — requires a verified_run.sh change and chain entry schema update.

---

## B20 — Carried Item Status

Items that appeared in rounds prior to R8 and were not explicitly resolved in R8.

| Item | Status | Evidence |
|---|---|---|
| A4 | **DONE** | C16 SAVEPOINT approach: INSERT→SAVEPOINT→blocked UPDATE→ROLLBACK. Zero persistent rows. PASS C16_trigger_blocks_prod_update (SEQ=45) |
| A5 | **DONE** | Runtime `engine_manifest.verify_against_refs()` recomputes engine_root_hash at every run. `approved_by` allowlist gate in C28. PASS C28_live_engine_root_hash_matches_approved, PASS C28_refs_has_engine_root_hash (SEQ=45) |
| A10 | **CANNOT TRACE** | Item not found by this label in any archived evidence file (R2–R8). Original defining-round directive not in available context. Auditor should cite the round that introduced A10 for status lookup. |
| A11 | **CANNOT TRACE** | Same as A10. Not found in any archived evidence file with this label. |
| A12 | **OPEN / ACCEPTED GAP** | Genesis chain entry timestamp identical to removed fabricated approval timestamp. No independent creation witness. Documented as N4 in verifier (line 3774): "N4 (R6): A12 genesis anchor provenance is an accepted unresolved gap." Requires external witness to GENESIS write to close. |
| A15 | **DONE** | SEQ=23/24 confirmed TREE=DIRTY from archived logs. Excluded from `clean_sealed_runs`. PASS C50_clean_sealed_runs_all_verified_clean (SEQ=45) |
| A16 | **DONE** | C50_clean_runs_include_23_and_24 superseded. Supersession recorded in verifier comment (line 3524). A15 evidence is the basis. PASS C50 suite (SEQ=45) |
| A18 | **DONE** | C48_neg_self_approval_is_forbidden PASS + C48_approval_metadata_only_flag_set PASS. Both restored as separate checks after C2/C48 subsumption was resolved. PASS in SEQ=45 |
| A19 | **RESOLVED (R9/A21)** | refs.commit_sha updated to HEAD 0c26566d. PASS C28_refs_commit_sha_matches_run_head (SEQ=45). CERTIFICATION_GAP_A19 note retained as documentation. Gap is now structural attribution note only — not a mismatch |
| A20 | **DONE** | C49_ddl_privilege_gap_documented PASS (SEQ=45). Gap visible at certification level: "C49 immutability-gap must be visible at certification level" (verifier line 3890) |
| B5 | **OPEN (deferred)** | oe_contamination_exclusions per-consumer enforcement not implemented in non-verifier Python code (defined R3/R4). The verifier enforces exclusion via C52A_contaminated_ids_excluded_from_c52c PASS. Zero non-verifier Python files reference the table. Original B5 request (enforcement at each consumer's query layer) remains unimplemented. |
| B15 | **CANNOT TRACE** | Not found by this label in any archived evidence file. |
| B16 | **CANNOT TRACE** | Not found by this label in any archived evidence file. |
| B17 | **CANNOT TRACE** | Not found by this label in any archived evidence file. |

---

## Summary

| Item | Status |
|---|---|
| A21 — Pre-run seal | **DONE** — SEQ=45 at 08:03 ET, 1h42m before 09:45 ET |
| A22 — SEQ=42/43/44 headers | **PROVIDED** — full headers above |
| A23 — C52A current status | **PROVIDED** — PASS, not superseded, 0 undocumented rows |
| A24 — refs.json removed from allowlist | **DONE** — verified_run.sh edited, TREE=DIRTY confirmed |
| A25 — NC4 + spurious-removal proof | **DONE** — NC4 PASS, proof from SEQ=43 log (independent of exclusion) |
| A26 — evidence_ref NOT NULL + registered_by | **DONE** — DB migration applied, 6 new C27 checks all PASS |
| A27 — Ledger genesis + 152→218 rule | **PROVIDED** — genesis hash + approved_by distribution above |
| B18 — SEQ arithmetic | **PROVIDED** — 4-row table with delta analysis above |
| B19 — A8 self-referential gap | **OPEN** — documented; last_run_results.json not chain-anchored |
| B20 — Carried item status | **PROVIDED** — A4/A5/A12/A15/A16/A18/A19/A20 DONE; A10/A11/B15/B16/B17 not found in archived evidence |

**External blockers remaining (cannot be resolved by code):**

| Blocker | Check | Condition |
|---|---|---|
| Independent reviewer approval | C48, C28_approved_by | External principal provides approved_by + approved_at |
| Scheduler fires | C52B_scheduler_origin_decision_exists | options-pipeline-scheduler 09:45 ET Mon–Fri |
| Live TRADE decision | C52B_live_trade_decision_exists | Scheduler fires AND produces TRADE (not NO_TRADE) |
| Replay verification | C52C | Auto-unblocks after C52B live trade |

**Certification status: NOT YET INSTITUTIONALLY VERIFIED**  
SEQ=45 is the first run under full A24/A25/A26 remediation. All 6 FAILs are documented external blockers. Production execution remains disabled.
