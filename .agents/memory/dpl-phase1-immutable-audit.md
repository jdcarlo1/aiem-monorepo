---
name: DPL Phase 1 — Immutable Audit Record
description: oe_decision_audit table design, trigger immutability model, integrity gate, verifier pattern
---

# DPL Phase 1 — Immutable Audit Record

## Table
`oe_decision_audit` — 9 columns: decision_id (PK), parent_id (FK→self, nullable),
created_at (TIMESTAMPTZ), input_hash, output_hash, verification_status
(CHECK IN 'VERIFIED'/'PENDING'/'TAMPERED'), engine_version, db_version, is_test_record.

## Immutability model
Trigger `trg_oe_dpl_immutable` / function `_oe_dpl_guard_immutability`:
- `is_test_record=TRUE` rows: DELETE and UPDATE freely permitted (enables test cleanup).
- `is_test_record=FALSE` rows: DELETE always blocked; UPDATE blocks any core column change;
  only `verification_status` may be updated.

**Why:** test cleanup requires DELETE; production audit rows must be immutable;
decoupling by is_test_record avoids TRUNCATE on live data.

## Reject-on-integrity-failure gate
`_post_write_integrity_check(cur, decision_id, expected_input_hash, expected_output_hash)`
re-reads stored hashes immediately after INSERT and raises `ValueError` on mismatch.
Called inside `write_decision` before the final VERIFIED UPDATE + commit.

## Live version sourcing (no hardcoded literals)
- `engine_version`: `SELECT version_id FROM oe_model_versions WHERE is_active=TRUE AND is_test_record=FALSE LIMIT 1`; fallback constant `_ENGINE_VERSION_FALLBACK = "no_active_champion"`.
- `db_version`: `SELECT split_part(version(), ' ', 2)`; fallback `_DB_VERSION_FALLBACK = "unknown"`.

## Verifier
`verify_dpl_phase1.py` — 44 ACs; SEQ=2 PASS=44 FAIL=0.
Pre-run cleanup: `DELETE FROM oe_decision_audit WHERE is_test_record = TRUE` (trigger allows).

## Scheduler wiring
`aiem_options_scheduler.py` lines 762-770: non-fatal try/except, same pattern as phase4/phase5.

## Scope isolation
No D1/D2/D3 tables or pipeline code. No execution-quality fields (paper-mode only).
Phase 2/3 will add decision-content capture.
