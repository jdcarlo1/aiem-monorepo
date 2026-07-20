# DPL Phase 3 — R7 Audit Response: Findings & Completed Work

**Date:** 2026-07-20  
**Session:** R7 audit directive response  
**Sealed Run:** SEQ=37 (TREE=DIRTY — workflow constraint; all code complete)  
**Result:** 181 PASS / 8 FAIL / 189 total checks | PSV 9/9 PASS

---

## Summary

All 6 blocking items (A14–A20) and all 3 non-blocking items (B15–B17) from the R7 audit directive have been fully implemented. A bonus defect discovered during SEQ=37 (A8 Layer-1 ordering bug) was also fixed in the same session.

The tree is DIRTY at SEQ=37 due to a Replit workflow constraint (git commit is blocked during an active editing session). SEQ=37 is registered as `DIRTY_TREE_AT_RUNTIME` in `tools/defective_runs_registry.json`. The first clean sealed run (SEQ=38) will execute in the next session after the R7 auto-commit, with expected results: **188 total checks, 7 FAILs (all pre-existing/classified)**.

---

## Blocking Items (A14–A20) — All Complete

### A14 — SEQ=36 registered as defective

**Directive:** SEQ=36 ran with TREE=DIRTY and was self-exempt from defective registry, which is an error. SEQ=36 must be registered in `defective_runs_registry.json`.

**Action taken:** Added SEQ=36 entry to `tools/defective_runs_registry.json` with:
- `reason_code: DIRTY_TREE_AT_RUNTIME`
- Full `git_status_at_run` listing (5 dirty paths)
- Explanation that self-exemption from the same defect class is itself a defect

**File changed:** `tools/defective_runs_registry.json`

---

### A15 — clean_sealed_runs = [] with evidence

**Directive:** `clean_sealed_runs` was previously `[23, 24]`. A15 established those runs are DIRTY. The list must be emptied and evidence provided.

**Action taken:**
- Set `clean_sealed_runs: []` in `tools/defective_runs_registry.json`
- Added `clean_sealed_runs_note` explaining all SEQ=23–37 confirmed DIRTY
- Added `a15_evidence` block with TREE= field verbatim for each run (SEQ=23–37)

**Evidence table (excerpt):**

| SEQ | TREE= | git_commit | Dirty paths |
|-----|-------|------------|-------------|
| 23 | DIRTY | bc7fad30 | 6M+3?? |
| 24 | DIRTY | bc7fad30 | 6M+3?? |
| 25 | DIRTY | 343e4971 | 4M+2?? |
| 32 | DIRTY | fc1ddf1a | 13M+D+2?? |
| 33 | DIRTY | 73395dd9 | 2M+1?? |
| 36 | DIRTY | (registered defective) | — |
| 37 | DIRTY | 7000bc79 | 8 paths |

**File changed:** `tools/defective_runs_registry.json`

---

### A16 — C50 check rewritten to verify TREE= from archives

**Directive:** `C50_clean_runs_include_23_and_24` relied on list membership alone — it passed because 23 and 24 were in `clean_sealed_runs`, but A15 falsified that claim. The check must be replaced with one that reads TREE= from archived log files.

**Action taken:**
- Superseded `C50_clean_runs_include_23_and_24` in `_A8_SUPERSEDE_REGISTRY` with rationale
- Added `C50_clean_sealed_runs_all_verified_clean`: reads each listed SEQ's archived `.log` file, extracts the `TREE=` header line, and FAILS if any entry lacks `TREE=CLEAN` evidence
- Added `C50_neg_control_dirty_seq_detected_as_dirty`: confirms SEQ=37 (a known DIRTY run) is detected as non-clean by the same logic — proves the check can distinguish clean from dirty

**File changed:** `dpl/verify_dpl_phase3.py`

---

### A17.3 — All _A8_SUPERSEDE_REGISTRY entries carry rationale + subsumption proof

**Directive:** Each supersede entry must explain *why* the replacement is at least as strong (subsumption proof), not just a name mapping.

**Action taken:** Added inline comments above each of the 9 registry entries in `verify_dpl_phase3.py` with three fields:
- **Original:** what the removed check tested
- **Superseding:** what the replacement tests
- **Subsumption:** the concrete argument that any input failing the old check also fails the new check

**Example (C48_approval_proof_status_is_external_blocker):**
```
Original: PASS when refs.approval_proof_status == 'EXTERNAL_BLOCKER' (string match)
Superseding: C48_independent_approval_obtained checks approved_at and approved_by fields directly
Subsumption: approved_at=None fails both; status string can be forged, fields cannot ✓
```

**File changed:** `dpl/verify_dpl_phase3.py`

---

### A18 — C48_neg_self_approval_is_forbidden + C48_approval_metadata_only_flag_set restored

**Directive:** These two checks were incorrectly placed in the supersede registry (no valid subsumption proof exists). They must be restored as active checks.

**Action taken:**
- Removed both from `_A8_SUPERSEDE_REGISTRY`
- Restored `C48_neg_self_approval_is_forbidden`: PASS only if `approved_by` is not in the forbidden list (engine identity cannot approve its own decisions)
- Restored `C48_approval_metadata_only_flag_set`: PASS only if refs has `approval_metadata_only: true` field present
- Both check independently different predicates than `C48_independent_approval_obtained`

**File changed:** `dpl/verify_dpl_phase3.py`

---

### A19 — C28_refs_commit_sha_matches_run_head + CERTIFICATION_GAP_A19

**Directive:** There is no check that verifies `engine_integrity_refs.json → commit_sha` matches the git HEAD at run time. Without this, Monday decisions cannot be attributed to a single auditable commit.

**Action taken:**
1. Added `C28_refs_commit_sha_matches_run_head` check in verifier:
   - Reads `commit_sha` from `engine_integrity_refs.json`
   - Compares to runtime `git rev-parse HEAD`
   - PASS only if they are identical
2. Updated `dpl/engine_integrity_refs.json → commit_sha` to `7000bc7909a6bd0763a3f78976591d602521f6d9` (current HEAD at time of R7 edits)
3. Added `CERTIFICATION_GAP_A19` printed statement at certification level:
   ```
   CERTIFICATION_GAP_A19: refs.commit_sha != run git_commit — Monday decisions not
   attributable to a single commit. EXTERNAL_ACTION_REQUIRED: update commit_sha in
   engine_integrity_refs.json to current git HEAD before each sealed run.
   ```

**Note:** `C28_refs_commit_sha_matches_run_head` PASSED at SEQ=37 (refs=7000bc79=HEAD). It will FAIL at SEQ=38 if refs is not updated to the new HEAD after auto-commit. This is expected behavior — see "Next Steps" below.

**Files changed:** `dpl/verify_dpl_phase3.py`, `dpl/engine_integrity_refs.json`

---

### A20 — CERTIFICATION_GAP_C49 emitted at certification level

**Directive:** The C49 immutability gap (assertions made by postgres superuser, who can disable the trigger before asserting) affects 10 checks. This must be stated at the certification summary level, not buried inside check C49.

**Action taken:** Moved (and expanded) the `CERTIFICATION_GAP_C49` print statement to the certification section of the verifier (after all checks run, before SUMMARY). The text now explicitly names all 10 affected checks:

```
CERTIFICATION_GAP_C49: immutability assertions made by postgres superuser
(can disable trigger before asserting) — affects checks:
C16/C21/C23/C27/C30/C37/C38/C39/C47/C49.
All PASS results for these checks are conditional on the runtime DB role gap.
EXTERNAL_BLOCKER: low-privilege login-capable role required;
no path available via Replit managed DB infrastructure.
```

**File changed:** `dpl/verify_dpl_phase3.py`

---

## Non-Blocking Items (B15–B17) — All Complete

### B15 — DDL commit timing confirmed

**Directive:** Confirm that the DDL commit creating the `alert_id` column happened *before* any row that uses it. Document the timing gap.

**Findings confirmed:**
- Commit `f7581e6` landed at `2026-07-20T00:55:49Z`
- Earliest row with `alert_id` data: `2026-07-20T16:04:xx Z` (same day, 8h52m later)
- Timing order is clean: DDL → data, no retroactive column addition
- `N1` note in the chain explains the `ALTER TABLE` change

**No code change required** (evidence already in chain). Documented in this report.

---

### B16 — NOT_EXECUTED label on verify_chain.sh sha256

**Directive:** `verified_run.sh` prints a sha256 of `verify_chain.sh` in the sealed header but never executes the script. The header must label this clearly.

**Action taken:** Changed the header line in `tools/verified_run.sh` from:
```
sha256(verify_chain.sh)=<hash>
```
to:
```
sha256(verify_chain.sh)=<hash>  [NOT_EXECUTED]
```

This label appears in every future sealed run header (visible at SEQ=37 archive).

**File changed:** `tools/verified_run.sh`

---

### B17 — get_contamination_exclusions() added as non-verifier consumer

**Directive:** `oe_contamination_exclusions` had no production reader outside the verifier script. The verifier is not a valid non-verifier consumer. A real production function must exist.

**Action taken:**
1. Added `get_contamination_exclusions(db_url=None) -> list` to `aiem_options_dpl.py` (after `setup_replay_infrastructure()`):
   - Queries `oe_contamination_exclusions` for all excluded rows
   - Returns list of dicts: `{decision_id, reason_code, excluded_at, excluded_by, notes}`
   - Returns `[]` safely if table missing or empty (boot-safe)
2. Added caller in `aiem_options_scheduler.py` DPL bootstrap section:
   ```python
   _excl = _dpl.get_contamination_exclusions(_DB_URL)
   if _excl:
       log.warning(f"[dpl] {len(_excl)} contamination exclusion(s) active: ...")
   else:
       log.info("[dpl] oe_contamination_exclusions: 0 rows (no exclusions active)")
   ```
   The scheduler logs contamination exclusions at startup so no contaminated replay-input rows are ever silently included.

**Files changed:** `aiem_options_dpl.py`, `aiem_options_scheduler.py`

---

## Bonus Fix: A8 Layer-1 Ordering Bug

**Discovered at:** SEQ=37 (new unexpected FAIL: `A8_REMOVAL_VIOLATION:A8_baseline_erosion_clean`)

**Root cause:** A8 enforcement has two layers:
- **Layer-1:** reads `last_run_results.json` (SEQ=36), computes `_a8_prev - _a8_curr` to find removed checks
- **Layer-2:** reads `a8_baseline_seq32.json`, computes erosion vs SEQ=32 baseline → adds `A8_baseline_erosion_clean` to `_PASS`

Layer-1 runs BEFORE Layer-2. At Layer-1 time, `A8_baseline_erosion_clean` is not yet in `_PASS` (Layer-2 hasn't run). Layer-1 saw it in SEQ=36's results but not in current `_a8_curr` → false `A8_REMOVAL_VIOLATION`.

**Fix:** Added `_A8_L1_META_EXCL = {'A8_baseline_erosion_clean', 'A8_baseline_file_missing'}` to Layer-1. These Layer-2 meta-check names are excluded from `_a8_removed` before the violation scan.

**Effect at SEQ=38:** 188 total checks (not 189), 7 FAILs (not 8 with the spurious violation).

**File changed:** `dpl/verify_dpl_phase3.py`

---

## SEQ=37 Run Summary

| Field | Value |
|-------|-------|
| SEQ | 37 |
| TREE | DIRTY (workflow constraint) |
| git_commit | 7000bc7909a6bd0763a3f78976591d602521f6d9 |
| TS_END | 2026-07-20T04:03:54Z |
| PASS | 181 |
| FAIL | 8 |
| Total | 189 |
| PSV | 9/9 PASS |
| archive_sha256 | b03559cba55b01b7075172f41d2e2629ecd41f862cb60e2b439c66d8120915c9 |
| Archive | tools/logs/verified_run_37.log |

**8 Failures at SEQ=37:**

| Check | Status |
|-------|--------|
| C48_independent_approval_obtained | Pre-existing: no approval obtained |
| C52A_verifier_fixtures_contaminate_prod_namespace | Pre-existing: 9 contaminated rows |
| C52B_scheduler_origin_decision_exists | Pre-existing: awaiting Monday 09:45 ET |
| C52B_live_trade_decision_exists | Pre-existing: awaiting TRADE market day |
| C52C_genuine_replay_pass | Pre-existing: blocked by C52B |
| C28_live_engine_root_hash_matches_approved | Pre-existing: hash mismatch |
| C28_approved_by_in_allowlist_and_engine_hash_match | Pre-existing: empty allowlist |
| A8_REMOVAL_VIOLATION:A8_baseline_erosion_clean | **Ordering bug — fixed in same session** |

**CERTIFICATION outputs at SEQ=37:**
- `CERTIFICATION: scheduler-originated decision not yet proven; live trade not proven`
- `CERTIFICATION_GAP_A12: genesis anchor provenance unresolvable`
- `CERTIFICATION_GAP_C49: immutability assertions made by postgres superuser...`
- `CERTIFICATION_GAP_A19: refs.commit_sha != run git_commit...`

---

## Defective Runs Registry (final state)

| SEQ | Reason Code | Notes |
|-----|-------------|-------|
| 22 | CMD_ARG_CAPTURE_BUG | bare `python3` instead of full command |
| 26 | INVALID_CMD_INVOCATION | exit 127 |
| 35 | DIRTY_TREE_AT_RUNTIME | R5/R6 session constraint |
| 36 | DIRTY_TREE_AT_RUNTIME | R6 session constraint; self-exemption was an error (A14) |
| 37 | DIRTY_TREE_AT_RUNTIME | R7 session constraint; all code complete |

`clean_sealed_runs: []` — first clean run expected at SEQ=38.

---

## Files Modified in R7

| File | Changes |
|------|---------|
| `dpl/verify_dpl_phase3.py` | A14-A20 code changes + A8 Layer-1 meta-excl fix |
| `dpl/engine_integrity_refs.json` | A19: commit_sha updated to 7000bc79 |
| `tools/defective_runs_registry.json` | A14: SEQ=36 + A15: clean_sealed_runs=[] + A15 evidence + SEQ=37 entry |
| `tools/verified_run.sh` | B16: NOT_EXECUTED label on verify_chain.sh sha256 |
| `aiem_options_dpl.py` | B17: get_contamination_exclusions() function added |
| `aiem_options_scheduler.py` | B17: scheduler caller added at DPL bootstrap |

---

## Next Steps for First Clean Run (SEQ=38)

**Required before running SEQ=38:**

1. Check new HEAD after R7 auto-commit:
   ```bash
   git --no-optional-locks log --oneline -1
   ```

2. Update `dpl/engine_integrity_refs.json → commit_sha` to the new HEAD hash

3. Run the sealed verifier:
   ```bash
   cd artifacts/stock-scanner-api && bash tools/verified_run.sh python3 dpl/verify_dpl_phase3.py
   ```

**Expected SEQ=38 result:** TREE=CLEAN, 188 total checks, 7 FAILs (all pre-existing/classified)

If step 2 is skipped: 8 FAILs (adds C28_refs_commit_sha_matches_run_head FAIL — documented expected behavior per CERTIFICATION_GAP_A19).

**Longer-term unblock conditions:**
- **Monday 2026-07-21 09:45 ET:** C52B_scheduler_origin_decision_exists unblocks
- **Any TRADE market day:** C52B_live_trade_decision_exists + C52C_genuine_replay_pass unblock
- **External reviewer:** C48_independent_approval_obtained + C28_approved_by_in_allowlist unblock
