---
name: Options Engine Phase III Phase 2
description: Strategy/Decision/Outcome Capture — 6 tables, 42 strategies, CHECK constraints, wiring points
---

## Tables (all oe_ prefix)
- oe_strategy_registry: 42 canonical strategies seeded on bootstrap
- oe_strategy_candidates: every strategy per pipeline run (chain + LONG_CALL + LONG_PUT always)
- oe_counterfactual_snapshots: frozen chain data at decision time (point-in-time, no look-ahead)
- oe_counterfactual_outcomes: post-close alternative P&L — is_hypothetical CHECK constraint DB-enforced
- oe_decision_records: APPROVE/REJECT/NO_TRADE/SUBSTITUTE per run
- oe_trade_records: full entry/exit lifecycle per alert

## CHECK Constraints (live-proven)
- oe_cf_outcome_is_hypothetical: CHECK (is_hypothetical = TRUE) — CheckViolation on FALSE
- oe_cf_outcome_calculated_after_snapshot: CHECK (calculated_at >= '2026-01-01')

## Wiring points in scheduler
1. After Phase 1 init block (line ~720): bootstrap_phase2(_DB_URL)
2. After Stage EI REGISTRY block (line ~1062): capture_strategy_candidates
3. After Stage 6 REGISTRY block (line ~1540): capture_decision_record (before NO_TRADE early return)
4. After Stage 8 REGISTRY block (line ~1664): capture_counterfactual_snapshot + capture_trade_record + update_decision_alert_id

## Wiring in pipeline (grade_options_outcomes)
- After Phase 1 outcome update: calculate_counterfactual_outcomes + update_trade_record_exit

## Decision type classification
- APPROVE: direction in (LONG_CALL, LONG_PUT), no gate failures
- REJECT: gate_failures non-empty
- NO_TRADE: direction=NO_TRADE, score/margin gates failed (no gate failures)
- SUBSTITUTE: best_chain_strategy.strategy != REQ6 direction

## Verifier
- verify_phase3_phase2.sh: PASS=64 FAIL=0 SKIP=0 EXIT=0 (2026-07-18)
- script_sha256=7916ec3e927d531348bd5639e08e09d5631b1c8af5aaeee98d9c672cbb9640cf

## Why these design choices
- non-fatal: every Phase 2 call in try/except so pipeline is never blocked
- point-in-time: counterfactual uses chain data frozen at decision (oe_counterfactual_snapshots)
- look-ahead prevention: calculate_counterfactual_outcomes only called from grade_options_outcomes (post-expiry)
- DB enforced: CHECK constraint not just application-level; tested live with CheckViolation proof
