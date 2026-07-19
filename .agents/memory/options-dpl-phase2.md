---
name: DPL Phase 2 — Decision-Context Capture
description: Phase 2 of the Decision Proof Layer; five JSONB context blobs wired into oe_decision_audit and the scheduler.
---

## What was built
- Five nullable JSONB columns added to `oe_decision_audit` via idempotent `ALTER TABLE IF NOT EXISTS`:
  `identity_json`, `technical_json`, `options_intel_json`, `probability_risk_json`, `justification_json`
- Trigger `_oe_dpl_guard_immutability` extended to block UPDATE of all five new columns on production rows
  (same rule as core Phase 1 fields — only `verification_status` remains mutable)
- `assemble_dpl_context()` in `aiem_options_dpl.py` assembles all five blobs from in-memory pipeline data
  plus three DB lookups (oe_knowledge_base, oe_options_metrics, oe_strategy_candidates, oe_portfolio_context)
- `write_decision()` extended with `context: Optional[dict] = None`; backward-compatible (None = Phase 1 behaviour)
- Two scheduler wiring points in `_execute_job` (aiem_options_scheduler.py):
  - TRADE path: after `update_decision_alert_id`, before `options_engine_runs` write
  - NO_TRADE path: before the `return` that exits the function

## Flagged fields (no live per-decision source — explicit `_flag` key in JSONB)
| Field | Flag | Reason |
|---|---|---|
| `capital_preservation_score` | NOT_PER_DECISION | `oe_strategy_scorecards.capital_efficiency` is historical aggregate |
| `capital_efficiency_score` | NOT_PER_DECISION | same |
| `time_based_exit_rules` | PARTIAL | DTE captured; structured time-decay exit rules not pre-computed |
| `adjustment_rolling_rules` | NOT_COMPUTED | no structured rolling/adjustment criteria in pipeline |
| `invalidation_conditions` | PARTIAL | `alert_fields.main_risks` (free-text) captured only |

## Field source map (key)
- `identity_json.market_regime` ← `stock_data["market_regime"]` = gex_regime from Stage 2
- `identity_json.volatility_regime` ← `ivr_result["iv_label"]` from `compute_iv_rank_live` Stage 3
- `technical_json.trend` ← `mtf_result["dominant_bias"]` Stage 4
- `options_intel_json.greeks` ← `sel_data[delta/gamma/theta/vega]` Stage 3
- `options_intel_json.liquidity_score` ← DB: `oe_strategy_candidates.liquidity_score` WHERE `selected=TRUE AND trace_id=...`
- `probability_risk_json.portfolio_risk_engine_output` ← DB: `oe_portfolio_context` WHERE `trace_id=...`
- `justification_json.stop_loss_criteria.stop_level` ← `sel_data["stop_level"]` Stage 3
- `justification_json.no_trade_explanation` ← populated only when `direction == "NO_TRADE"`

## psycopg2 / JSONB gotcha
psycopg2 auto-deserialises JSONB columns to Python dicts on SELECT. Never call `json.loads()` on JSONB rows — that raises `TypeError: not str/bytes/bytearray`.

## Verifier
`verify_dpl_phase2.py` — 56 PASS / 0 FAIL (2026-07-19).
Tests: schema (C01-C05), trigger (C06-C08), bootstrap idempotence (C09), write+context (C10-C12), identity (C13-C18), technical (C19-C22), options_intel (C23-C27), probability_risk (C28-C33), justification (C34-C38), bonus NO_TRADE + backward-compat checks.
