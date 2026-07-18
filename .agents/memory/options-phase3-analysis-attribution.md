---
name: Options Engine Phase 3 — Analysis & Attribution
description: 9 new tables (Sections 9-14); key design decisions for BH-FDR, KB confidence gate, scorecard boundary, grep-c verifier bug.
---

# AIEM Options Engine Phase 3 — Analysis & Attribution

## Tables (9 new, all prefixed oe_)
oe_root_cause_records, oe_attribution_runs, oe_indicator_attribution,
oe_interaction_hypotheses, oe_interaction_results, oe_strategy_scorecards,
oe_knowledge_base, oe_kb_confidence_log, oe_regime_performance

## BH-FDR correction
Pure Python, no scipy. `_bh_fdr_correction(p_values, alpha)` is step-up.
Known-answer test vector: p=[0.039,0.001,0.210,0.008,0.041] → [False,True,False,True,False].
Applied before accepting ANY attributed indicator or interaction hypothesis.
Min sample gate: n≥20 before any statistical claim.

## Fisher exact test
Pure Python via log-gamma (math.lgamma). `_fisher_exact_p(a,b,c,d)`.
Two-sided, hypergeometric PMF. No scipy dependency.

## KB confidence gate
- Initial confidence always 50 (neutral).
- Increase: requires validated_oos=True AND sample_size≥20.
- Decrease: requires sample_size≥20.
- Every attempt (pass or fail) is audit-logged in oe_kb_confidence_log with gate_passed.
- assert_kb_confidence_gated() checks DB STATE (confidence_score≠50 with statistical_gate_passed=FALSE),
  NOT the audit log. Blocked attempts intentionally appear in the log with gate_passed=FALSE — that is CORRECT, not a violation.

## Scorecard aggregation boundary
UNIQUE(strategy_id, segment_type, segment_value).
_assert_no_cross_strategy_aggregation([...]) raises ValueError if len(unique)>1.

## Wiring points
- scheduler.py line 731: p3 import block (alongside p2)
- scheduler.py line 1591: NO_TRADE → record_root_cause + add_knowledge_base_entry
- scheduler.py line 1999: grade_outcomes_job → record_root_cause_batch + rebuild_all_scorecards
- pipeline.py line 732: grade_options_outcomes → record_root_cause per closed trade

## Verifier
verify_phase3_phase3.sh — 41 checks, PASS=41 FAIL=0.
Run: bash tools/verified_run.sh "bash verify_phase3_phase3.sh"

## grep -c verifier bug (CRITICAL: applies to all future verifiers)
`grep -c "pattern" file 2>/dev/null || echo 0` produces "0\n0" when pattern not found
(grep -c returns exit=1 but still prints "0", then || echo 0 appends second "0").
Fix: use `grep -c "pattern" file 2>/dev/null || true` (no echo 0 fallback needed).

## Sunday 3PM analytics batch (NOT YET SCHEDULED)
run_attribution_batch(), run_interaction_tests(), rebuild_regime_matrix(), rebuild_all_scorecards()
are all implemented but not yet wired to a scheduled job. See follow-up task for wiring.
