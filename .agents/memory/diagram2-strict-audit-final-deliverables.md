---
name: Diagram 2 strict 5-status audit — final deliverables and new findings
description: How the final diagram2_component_inventory.json/matrix.csv were verdicted under the strict PASS/FAIL/BLOCKED/NOT_EXECUTED_WITH_VALID_TRIGGER_EVIDENCE/ARCHITECTURAL_DISCREPANCY enum, and major findings not yet in other topic files.
---

## Deliverables
`artifacts/stock-scanner-api/diagram2_component_inventory.json` and `diagram2_component_matrix.csv` hold the C1-C30 verdicts under the user's strict 5-value status enum (no compound/dual statuses; nuance goes in `failure_reason`/`discrepancy_notes`). Final distribution: 9 PASS, 8 ARCHITECTURAL_DISCREPANCY, 7 NOT_EXECUTED_WITH_VALID_TRIGGER_EVIDENCE, 6 FAIL, 0 BLOCKED.

## Trace-ID genuine-vs-test filtering rule
`aiem_diagram2_trace_audit` mixes genuine production trace_ids (pattern `aiem_YYYY_MM_DD_TICKER_hex`) with synthetic ones (`D2_MUTATION_*`, `*TEST*`, `*VERIFICATION*`). Always filter these out (`trace_id NOT ILIKE '%MUTATION%' AND trace_id NOT ILIKE '%TEST%'`) before citing a row as genuine runtime proof — a naive query on this table will silently pull mutation-testing fixtures.

**Why:** an earlier pass in this same audit accidentally cited `D2_MUTATION_PASS_*`/`D2_MUTATION_FAIL_*` rows as real "Options / Smart Money" evidence before the pattern was caught; of 320 total trace rows, 172 are genuine, 34 mutation_test, 21 other_test, 93 unclassified "other".

## Layer 9 Statistical Edge — 100% batch failure (falsifies old PASS_LIVE_WIRED)
`layer9_scores` has 751 real rows; **100%** have a populated `error` column (two exact strings: `"'<' not supported between instances of 'int' and 'NoneType'"` on 511 rows, `"The truth value of a DataFrame is ambiguous..."` on 240). `hurst_raw` is *always exactly* 0.5, `vpin_raw` *always exactly* 0.3 (the hard-coded safe-defaults), `vrp_score`/`amihud_score`/`statistical_score` always NULL, `jump_detected` always false. This proves `compute_layer9_score()`'s outer try block (layer9_statistical_edge.py ~141-314) has never once completed successfully in the scheduled batch scanner across 751 consecutive invocations. Affects C9/C10/C11/C18/C19/C20.

Counter-signal not fully resolved: one unrelated live-query prediction (`aiem_probability_engine_predictions.id=458`, ticker MAA) shows a non-default `statistical_score=48.32` and `regime='trending'` from a *different* call context (`aiem_probability_engine/context.py`). Whether that path's sub-scores are genuinely computed or also silently defaulting (their weighted average of all-defaults lands suspiciously close to ~48) was not resolved — worth a follow-up if this component is revisited.

## Options structure — orchestrator fires but the specific persistence table is empty
Genuine production traces show a real "Options / Smart Money" stage firing with `status=PASS` (e.g. `aiem_2026_07_08_PLTR_f18372`, runtime_function=`module_scores_generated (options component)`). But `options_structure_scan` — the table `_compute_gex()`/`compute_options_structure()` in `aiem_options_structure.py` is supposed to write to — has **0 rows**, and `job_heartbeats` has no entry for any GEX/options job. Conclusion: *some* options scoring runs live, but the specific GEX/skew/term-structure numeric persistence claimed by the old audit cannot be confirmed — treat as ARCHITECTURAL_DISCREPANCY, not PASS, whenever this surfaces again.

## Specialist Council — 3 divergent specialist counts, none matching the "9" claim
`specialist_council.py` itself defines exactly 4 `SpecialistOpinion` calls (garch_volatility, macro_rates, bull_bear_debate, social_sentiment). Two separate live call sites in `main.py` (~41439/41446 and ~43754/43761) each independently construct only 2 `SpecialistOpinion` objects inline, bypassing the module's own function entirely. So the codebase has three divergent counts (4, 2, 2) and none reach the documented "9 specialists."

## Consolidation confirmed (no longer stale)
As of this pass, BH-FDR (`aiem_module5_discovery.py` / `aiem_module6_rediscovery.py`) and meta-learning trust (`main.py`) are genuinely consolidated to their canonical single implementations (`aiem_stat_tests.bh_fdr_reject`, `meta_learning_signal_trust.compute_ema_trust_update`) via thin delegating wrappers / direct calls — the old "DUPLICATED" flag for C2/C24 is resolved and should not be re-raised without new evidence.
