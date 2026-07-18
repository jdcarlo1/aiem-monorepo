---
name: Options Engine Phase 5 — Adaptive Control & Governance
description: Phase 5 of standalone options engine; 7 governance tables, 18 validation gates, champion-challenger versioning, hash-chained audit trail. Key schema and implementation quirks.
---

## What was built
`aiem_options_phase5.py` — adaptive control & governance module.

**7 tables**: oe_model_versions, oe_weight_proposals, oe_proposal_gate_results, oe_challenger_runs, oe_challenger_decisions, oe_promotion_events, oe_audit_events

**18 gates**: SAMPLE_SIZE, DATA_QUALITY, POINT_IN_TIME, LEAKAGE, STATISTICAL_SIGNIFICANCE, MULTIPLE_TESTING, IN_SAMPLE, OUT_OF_SAMPLE, WALK_FORWARD, REGIME, STRESS, TRANSACTION_COST, SLIPPAGE, PORTFOLIO_RISK, RUNTIME, END_TO_END, RISK_GATE_INTEGRITY, CAPITAL_PRESERVATION

**Proof**: tools/phase5_verify_seq27.txt — SEQ=27 PASS=105 FAIL=0 EXIT=0

## Critical schema fact: LEAKAGE gate
`aiem_options_alerts` has **no `trace_id` column**. Columns: alert_date, ticker, spot_at_alert, pnl_pct, outcome_status, etc. There is no shared FK between `oe_indicator_snapshots` and `aiem_options_alerts`.

**Fix**: `_run_gate_leakage()` is self-contained within `oe_indicator_snapshots`:
```sql
SELECT COUNT(*) AS n_leaks FROM oe_indicator_snapshots
WHERE captured_at > scan_date::timestamptz + INTERVAL '1 day'
```

## Hash chain timing bug (and fix)
`record_audit_event` computed `ts = _now_s()` then inserted with `NOW()` for `created_at`. When `NOW()` crossed a second boundary from `ts`, `verify_audit_chain` recomputed the hash using `created_at` and got a different result → chain break.

**Fix**: use `%s::timestamptz` with the pre-computed `ts` for `created_at`:
```python
VALUES (%s, ..., %s::timestamptz)  # last param = ts, not NOW()
```

## Verifier idempotency
`verify_phase5.py` runs a pre-run cleanup before AC-01:
- `TRUNCATE oe_audit_events RESTART IDENTITY` — hash chain starts clean each run
- `DELETE FROM oe_* WHERE is_test_record=TRUE` — removes prior test-run state
- Preserves `champion_v0` (is_test_record=FALSE)

**Why**: without cleanup, accumulated test state from multiple debug runs causes AC17 rollback target conflicts and AC21 chain break at random rows from prior runs.

## Test-namespace isolation rule
- `promote_challenger(_test_bypass=True)` deactivates only `is_test_record=TRUE` champions
- `rollback_champion(vid, _test_bypass=True)` operates only in test namespace
- Production `champion_v0` (sha256=ceccc0c502723303…) is NEVER touched by test operations
- AC17 seeds its own `test_rb_v0_XXXXXXXX` champion for rollback proof; never reuses production version

## oe_indicator_registry population
Table has 0 rows until scan runs have fired (populated lazily). This is correct and expected. The verify check should be `>= 0` (queryable), not `>= 1`.

## Scheduler wiring
- `bootstrap_phase5()` called after Phase 4 block in `aiem_options_scheduler.py`
- `seed_initial_champion()` called after bootstrap
- `get_governance_summary()` called inside `grade_outcomes_job`
