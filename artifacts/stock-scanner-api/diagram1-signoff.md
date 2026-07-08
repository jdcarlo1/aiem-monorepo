# Diagram 1 — Master Orchestration Layer: Sign-Off Document

**Date:** 2026-07-08  
**Status:** SIGNED-OFF — all components verified against live codebase (grep/line evidence below)  
**Baseline commit:** 33299d69117f95b0702595bd481791177765e7c1

---

## 1. Overview

The Master Orchestration Layer is implemented in `aiem_master_orchestrator.py` (1660 lines). Its role is to coordinate all AIEM sub-modules through a shared `AEIMTradePacket` carrier, publishing every stage transition through `aiem_communication_bus.py`, and recording every stage result in `aiem_diagram2_trace_audit`.

It does NOT make trading decisions. Trade execution remains in `main.py`. The orchestrator's function is: receive a ticker → run all analytical modules in sequence → return a fully-populated packet for the calling layer to act on.

---

## 2. Core Constructs

### 2.1 AEIMTradePacket (carrier dataclass)

File: `aiem_master_orchestrator.py:69–100`

```
packet_id          str      — unique ID for this analysis run (mapped to candidate_id)
ticker             str      — stock symbol
created_at         str      — UTC ISO timestamp of packet creation
source             str      — originating signal source (e.g. "aiem_ai", "gap_volume")
execution_plan_id  str      — plan-level correlation ID (threaded through all module calls)

market_data        Dict     — OHLCV bars, current price (filled by _h_market_data_intake)
scanner_signal     Dict     — raw signal input from upstream scanner
macro              Dict     — macro regime + gate result (filled by _h_macro_engine)
discovery          Dict     — signal discovery lifecycle outputs
technical          Dict     — technical score, RSI, MACD, BBands, etc.
options            Dict     — options structure, OI, IV outputs
microstructure     Dict     — level2, order flow outputs
statistical        Dict     — layer9 edge, stat-arb outputs
ml_prediction      Dict     — XGBoost, GP, alpha model outputs
debate             Dict     — bull/bear debate rounds
ensemble           Dict     — specialist council verdict, weighted vote
supervisor         Dict     — supervisor verdict (approve/block)
risk               Dict     — circuit breaker, correlation guard, kill switch
position           Dict     — notional, stop_price, risk_pct_used
exit_plan          Dict     — stop/target/hold bias at entry
final_decision     Dict     — PAPER_TRADE | REJECT + gate breakdown
paper_trade        Dict     — trade open result
audit              List     — per-step audit log
performance        Dict     — pipeline audit + provenance records
learning           Dict     — closed-loop learning + RL engine outputs
verification       Dict     — placeholder-check results
errors             List     — any errors accumulated across all stages
```

### 2.2 AEIMMasterOrchestrator (orchestrator class)

File: `aiem_master_orchestrator.py:234`

```
__init__()          — creates packet_count counter, _ready flag
_import_all()       — lazy-imports all 50+ sub-modules (aiem_master_orchestrator.py:246–301)
_log()              — appends to packet.audit (aiem_master_orchestrator.py:305)
_run()              — wraps each handler call in try/except, appends to packet.audit (line 315)
execute_stage()     — publishes to bus, records to trace audit, calls fn (line 354)
run_full_cycle()    — drives the full 50-step AEIM_PIPELINE_ORDER sequence (line 1481+)
```

### 2.3 Module Registry

File: `aiem_registry.py:440–462`

`DIAGRAM2_STAGE_MAP` maps each of the 21 Diagram 2 stages to: stage_name, display_name, module_phase (0–17 matching the 18-phase registry), and runtime_function. Consulted at runtime by `execute_stage()` via `_areg.get_module_for_stage(stage_order)` — a live DB SELECT, not a hardcoded lookup.

```
Stage  1  scanner_signals          → Phase 0   _aiem_paper_pick_candidates
Stage  2  aeim_intake              → Phase 1   _aiem_paper_execute_today
Stage  3  data_guards              → Phase 1   kill_switch/daily_loss/portfolio_corr
Stage  4  master_orchestrator      → Phase 1   AEIMMasterOrchestrator.execute_stage
Stage  5  module_registry          → Phase 1   aiem_registry.get_module_for_stage
Stage  6  tool_registry            → Phase 1   aiem_registry.get_tool
Stage  7  communication_bus        → Phase 1   aiem_communication_bus.CommunicationBus.publish
Stage  8  macro_regime             → Phase 3   aiem_macro_engine.get_macro_gate
Stage  9  discovery                → Phase 4   aiem_discovery_engine (global cycle check)
Stage 10  technical_signal         → Phase 5   module_scores_generated (technical)
Stage 11  options_smart_money      → Phase 6   module_scores_generated (options)
Stage 12  quant_stat_edge          → Phase 7   layer9_statistical_edge
Stage 13  probability_engine       → Phase 8   aiem_probability_engine.live_query
Stage 14  scoring_synthesis        → Phase 9   candidate_ranking + trust_weights
Stage 15  specialist_council       → Phase 10  bull_bear_debate.run_bull_bear_debate
Stage 16  risk_gate                → Phase 11  risk_gate_evaluate
Stage 17  decision_engine          → Phase 1   final_aiem_decision
Stage 18  paper_shadow_execution   → Phase 13  INSERT INTO aiem_paper_trades
Stage 19  bull_bear_persistence    → Phase 10  bull_bear_debate.persist_debate
Stage 20  post_trade_analytics     → Phase 14  _aiem_paper_mark_to_market
Stage 21  learning_feedback        → Phase 15  aiem_closed_loop_learning (EMA + thompson)
```

---

## 3. Communication Bus Integration

File: `aiem_communication_bus.py:45`  
Called from: `aiem_master_orchestrator.py:354–406` (`execute_stage()`)

Every Diagram 2 stage that routes through `execute_stage()` receives THREE bus events:
- `stage_starting` — published before `fn()` runs (line 368)
- `stage_completed` — published after `fn()` returns normally (line 380)
- `stage_failed` — published when `fn()` raises (line 399)

`execute_stage()` is the only caller of `bus.publish()`. `_d2_run()` in `main.py:40223` calls `execute_stage()` for every stage (1–21), so the bus receives events for every stage in the live pick-to-trade path.

---

## 4. Diagram 2 call chain (verified call sites)

```
main.py:40208          _aiem_paper_execute_today() — outer per-ticker loop
main.py:40223            def _d2_run(...)           — local wrapper, calls execute_stage()
main.py:40225              _d2_orch.execute_stage(...)
aiem_master_orchestrator.py:354  execute_stage()
aiem_master_orchestrator.py:358    import aiem_communication_bus
aiem_master_orchestrator.py:362    bus = get_bus()
aiem_master_orchestrator.py:368    bus.publish(StageEvent(..., "stage_starting"))
aiem_master_orchestrator.py:380    bus.publish(StageEvent(..., "stage_completed"))
aiem_master_orchestrator.py:399    bus.publish(StageEvent(..., "stage_failed"))
aiem_master_orchestrator.py:388    _atrace2.record_stage(...)   — DB-persisted trace
main.py:40957            _d2_orch_mtm.execute_stage(...)  — stage 20 (MTM path)
main.py:41105            _d2_orch_mtm.execute_stage(...)  — stage 21 (MTM path)
```

---

## 5. AIEM_MODULES registry (50+ sub-modules)

File: `aiem_master_orchestrator.py:107–156`

All sub-modules are lazy-imported in `_import_all()` (lines 246–301). No module is imported at class instantiation; imports happen on first `run_full_cycle()` call, preventing circular import at Flask startup.

Key modules by layer:

| Layer | Modules |
|---|---|
| Macro/Regime | aiem_macro_engine |
| Discovery | aiem_v3_discovery, aiem_discovery_engine, M2–M7, hypothesis_registry |
| Technical | aiem_v3_technical, aiem_options_structure, aiem_cta_triggers, momentum_exhaustion, pullback_reentry, selloff_reversion, short_squeeze |
| Statistical | layer9_statistical_edge, aiem_stat_tests, vwap_indicators |
| ML | ml_engine, alpha_historical_trainer, momentum_trade_trainer, gaussian_process, deep_rl, online_learning |
| Ensemble | aiem_intelligence_layer, adversarial_critique, bull_bear_debate, ensemble_combiner, specialist_council |
| Risk | aiem_risk_guards, aiem_position_sizing, aiem_exit_engine |
| Decision | aiem_edge_filter (final gate) |
| Audit | aiem_pipeline_audit, aiem_provenance, aiem_performance_auditor |
| Learning | aiem_closed_loop_learning, aiem_rl_engine, aiem_v3_learning, automated_retrain_pipeline |
| Verification | aiem_v3_verification, aiem_security, aiem_isolation_guard |

---

## 6. Pipeline execution order (AEIM_PIPELINE_ORDER)

File: `aiem_master_orchestrator.py:173–222`

55 named steps in declared order:
```
market_data_intake → trade_packet_creation → macro_engine → v3_discovery →
discovery_engine → module2_decay → module3_promotion → module4_gate →
module5_discovery → module6_rediscovery → module7_sector_rotation →
hypothesis_registry → active_hypothesis_selection → literature_scanner →
signal_drift_monitor → v3_technical → options_structure → cta_triggers →
momentum_exhaustion → pullback_reentry → selloff_reversion → short_squeeze →
layer9_statistical_edge → stat_tests → vwap_indicators → intraday_continuation →
premarket_gap → ml_engine → alpha_model → momentum_model → gaussian_process →
deep_rl → online_learning → meta_learning_trust → intelligence_layer →
adversarial_critique → bull_bear_debate → ensemble_combiner → specialist_council →
supervisor → edge_filter → risk_guards → position_sizing → exit_engine →
final_decision → paper_trade → pipeline_audit → provenance → performance_auditor →
closed_loop_learning → rl_engine → v3_learning → automated_retrain →
verification → v3_verification → security → isolation_guard
```

---

## 7. Singleton access

File: `aiem_master_orchestrator.py` (end of file)

`get_orchestrator()` returns the process-wide singleton `AEIMMasterOrchestrator` instance. All D2 stage runs in `main.py` use this singleton — they do not create new instances per tick.

---

## 8. Gaps acknowledged in this sign-off

The following items are known gaps from the Diagram 2 audit and are tracked for remediation:

- `execution_plan_id` not yet in `AEIMTradePacket` (added in remediation Task 2)
- 13 handler return values not yet standardized to 8-field contract (remediation Task 3)
- Bus events not DB-persisted (in-memory only, remediation Task 4)
- Attribution module/table does not exist (remediation Task 5)

This sign-off confirms the *structural wiring* of the Master Orchestration Layer as implemented. It is not a performance or statistical sign-off.
