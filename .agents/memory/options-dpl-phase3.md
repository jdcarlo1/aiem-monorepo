---
name: DPL Phase 3 — Reproducibility Replay
description: oe_decision_replay_inputs table + replay_decision() + 18-check verifier; Round 2 remediation complete; real-decision evidence pending Mon 2026-07-21 09:45 ET
---

# DPL Phase 3 — Reproducibility Replay

## Status
Implementation: COMPLETE (Round 2 remediation done; 18/18 PASS)
Real-decision replay evidence: PENDING (Monday 2026-07-21 09:45 ET)

## New table: oe_decision_replay_inputs
- PK = decision_id (FK → oe_decision_audit)
- Stores exact inputs to compute_req6_score(): contract_data_call, contract_data_put,
  stock_data_replay, iv_rank (0-1 float), verify_result_replay
- Version stamps: config_versions (req6_weights_hash, **scoring_fn_hash**), data_source_timestamps
- Stored scores: stored_call_score, stored_put_score, stored_direction
- **New columns (Round 2):** is_test_record BOOLEAN NOT NULL DEFAULT FALSE, scoring_weights_snapshot JSONB
- scoring_fn_hash stored INSIDE config_versions JSONB (no separate column)

## Replay function
- replay_decision(decision_id) loads stored inputs only (no live data)
- Re-runs compute_req6_score() for CALL and PUT
- Raises ReplayInputsMissingError (loud fail) if no row exists
- **NEW Round 2:** Raises ReplayCodeDriftError (CODE_DRIFT status) if scoring_fn_hash mismatch
  - CODE_DRIFT check skips when stored_fn_hash is falsy (old rows without hash)
- Direction thresholds mirror scheduler Stage 6: call>=put AND >=55 AND margin>=10
- NULL-safe matches: stored_call/put/direction = NULL → match=None, full_match=False
- Returns: full_match, call_match, put_match, direction_match, both scoring dicts

## R1 — Single weights source (Round 2)
- aiem_options_pipeline.py line 152: `_REQ6_SCORING_WEIGHTS = {...}` (ONLY definition)
- aiem_options_pipeline.py line 299: `weights = _REQ6_SCORING_WEIGHTS` (function uses it)
- aiem_options_dpl.py: `from aiem_options_pipeline import _REQ6_SCORING_WEIGHTS` (no local copy)
- compute_req6_score() returns "weights" key — C13 verifies they are identical objects

## R2 — Code-drift detection (Round 2)
- capture_replay_inputs() stores scoring_fn_hash = sha256(inspect.getsource(compute_req6_score))
- replay_decision() recomputes hash at replay time; raises ReplayCodeDriftError on mismatch
- Old rows (no fn_hash stored) are exempt — check skipped when stored_fn_hash is falsy
- C15: monkeypatched inspect.getsource triggers CODE_DRIFT; restoring → full_match=True

## R3 — Missing spec inputs documented
5 inputs not tracked before Round 2 (now all wired):
1. scoring_fn_hash (sha256 of compute_req6_score source at decision time, in config_versions)
2. scoring_weights_snapshot (full weights dict at decision time, separate JSONB column)
3. is_test_record (boolean: FALSE for prod rows, TRUE for test/verifier rows)
4. trigger-based immutability proof (trg_oe_replay_immutable)
5. NULL-safe match semantics (None ≠ False for missing stored scores)

## R4 — Table integrity (Round 2)
- Trigger: trg_oe_replay_immutable BEFORE DELETE OR UPDATE FOR EACH ROW
  - DELETE: allowed only when OLD.is_test_record = TRUE
  - UPDATE: allowed only when OLD.is_test_record = TRUE
  - Production rows (is_test_record=FALSE): both ops raise EXCEPTION
- bootstrap_dpl_phase3() migration order: ALTER TABLE → UPDATE test rows → CREATE TRIGGER
  (UPDATE runs before trigger exists so existing rows can be migrated safely)
- capture_replay_inputs() signature now has is_test_record=False parameter

## R5 — SEQ chain durability
- verified_run.sh sha256 UNCHANGED: 8146a523… (canonical; no modification)
- verify_chain.sh sha256 UNCHANGED: ca7896c7…
- SEQ counter resets per invocation (not cumulative) — this is correct by design

## R6 — Criterion 1 (real-decision evidence)
- BLOCKED until Monday 2026-07-21 09:45 ET (first production decision row)
- After cycle: query oe_decision_replay_inputs for first real row, call replay_decision(),
  confirm full_match=True for ≥2 decisions

## R7 — Procedural restoration authority
- All writes in Round 2 scoped to R4 directive: is_test_record column, scoring_weights_snapshot,
  scoring_fn_hash in config_versions, trigger creation
- No lateral writes; no restores executed
- Rule: any write not named in directive requires explicit approval first

## Key design facts
- compute_req6_score() is a pure function — identical inputs always produce identical outputs
- iv_rank is stored as 0-1 float (same value passed to compute_req6_score, not ×100)
- call_data / put_data are the exact dicts from scheduler lines 1353/1373
- bootstrap_dpl() calls bootstrap_dpl_phase3() automatically
- Verifier C12 mutation: theta 0.03→0.04 → D8: 40→20 → score delta = 1.6 (expected and observed)

## Verifier (18 checks, Round 2)
- verify_dpl_phase3.py — C01-C18
- Run: tools/verified_run.sh "python3 dpl/verify_dpl_phase3.py"
- C05: missing-evidence loud fail (ReplayInputsMissingError)
- C08-C10: replay match (call, put, direction)
- C12: mutation check (diff=1.6, expected_delta=1.6, theta 0.03→0.04)
- C13: single weights source (compute_req6_score "weights" == _REQ6_SCORING_WEIGHTS)
- C14: weight hash changes in-memory (orig f6b5f480…, mutated 2728db43…, restored f6b5f480…)
- C15: CODE_DRIFT negative control (monkeypatched getsource → ReplayCodeDriftError; restore → pass)
- C16: trigger blocks prod UPDATE (is_test_record=FALSE)
- C17: test rows have is_test_record=TRUE
- C18: NULL stored scores → match=None not False

## File sha256 after Round 2
- aiem_options_pipeline.py: bbcddcc13bd364bd…
- aiem_options_dpl.py: d36a36311ac2c429… (unchanged from Round 1 end state — all R1/R2/R4 already applied)
- verify_dpl_phase3.py: a3c747586bafbf46…
- tools/verified_run.sh: 8146a523… (UNCHANGED)
- verify_chain.sh: ca7896c7… (UNCHANGED)
