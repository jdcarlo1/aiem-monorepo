---
name: DPL Phase 3 — Reproducibility Replay
description: oe_decision_replay_inputs table + replay_decision() + 12-check verifier; pending evidence for real decisions (Monday 09:45 ET cycle)
---

# DPL Phase 3 — Reproducibility Replay

## Status
Implementation: COMPLETE (SEQ=3, 12/12 PASS)
Real-decision replay evidence: PENDING (Monday 2026-07-20 09:45 ET)

## New table: oe_decision_replay_inputs
- PK = decision_id (FK → oe_decision_audit)
- Stores exact inputs to compute_req6_score(): contract_data_call, contract_data_put,
  stock_data_replay, iv_rank (0-1 float), verify_result_replay
- Version stamps: config_versions (req6_weights_hash), data_source_timestamps
- Stored scores: stored_call_score, stored_put_score, stored_direction

## Replay function
- replay_decision(decision_id) loads stored inputs only (no live data)
- Re-runs compute_req6_score() for CALL and PUT
- Raises ReplayInputsMissingError (intentional loud fail) if no row exists
- Direction thresholds mirror scheduler Stage 6: call>=put AND >=55 AND margin>=10
- Returns: full_match, call_match, put_match, direction_match, both scoring dicts

## Key design facts
- compute_req6_score() is a pure function — identical inputs always produce identical outputs
- iv_rank is stored as 0-1 float (same value passed to compute_req6_score, not ×100)
- call_data / put_data are the exact dicts from scheduler lines 1353/1373 (base_fields + BS greeks + contract specifics)
- bootstrap_dpl() now calls bootstrap_dpl_phase3() automatically

## Scheduler wiring
- TRADE path (line ~1870): _dpl_trade_result = write_decision(...); then capture_replay_inputs()
- NO_TRADE path (line ~1704): _dpl_nt_result = write_decision(...); then capture_replay_inputs()
- Both paths are non-fatal (exceptions caught and logged, pipeline continues)

## Verifier
- verify_dpl_phase3.py — 12 checks C01-C12
- Must invoke as: tools/verified_run.sh "python3 verify_dpl_phase3.py"
  (single quoted argument — CMD=$1 only captures first arg)
- C05 = missing-evidence check (ReplayInputsMissingError)
- C08-C10 = replay match (call, put, direction)
- C12 = mutation check: theta 0.03→1.50, score diff = 3.20

## Pending evidence
After Monday 2026-07-20 09:45 ET cycle:
1. Query oe_decision_replay_inputs for first real row
2. Call replay_decision(decision_id) and compare to stored scores
3. Confirm full_match=True for 2 real decisions
EOF