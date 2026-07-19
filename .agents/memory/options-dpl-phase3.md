---
name: DPL Phase 3 — Reproducibility Replay
description: oe_decision_replay_inputs + replay_decision() + 26-check verifier; SEQ=14 EXIT=0; Criterion 1 BLOCKED; real-decision pending Mon 2026-07-20 09:45 ET
---

# DPL Phase 3 — Reproducibility Replay

## Status
Implementation: COMPLETE (R5.1–R5.4 done; C01–C26 PASS SEQ=14 EXIT=0)
Criterion 1 (real scheduler decision replayed): BLOCKED
Real-decision replay evidence: PENDING Monday 2026-07-20 09:45 ET  ← CORRECT DATE
Phase 3: OPEN

## New table: oe_decision_replay_inputs
- PK = decision_id (FK → oe_decision_audit)
- Stores exact inputs to compute_req6_score(): contract_data_call, contract_data_put,
  stock_data_replay, iv_rank (0-1 float), verify_result_replay
- Version stamps: config_versions (req6_weights_hash, scoring_fn_hash), data_source_timestamps
- Stored scores: stored_call_score, stored_put_score, stored_direction
- Columns: is_test_record BOOLEAN NOT NULL DEFAULT FALSE, scoring_weights_snapshot JSONB
- scoring_fn_hash stored INSIDE config_versions JSONB (no separate column)

## Replay function (Round 3 — fail-closed)
- replay_decision(decision_id) loads stored inputs only (no live data)
- Raises ReplayInputsMissingError (loud fail) if no row exists
- if stored_fn_hash is None → raises ReplayCodeDriftError(UNVERIFIABLE)
- if stored_weights_snap is None → raises ReplayCodeDriftError(UNVERIFIABLE)
- if live_fn_hash != stored_fn_hash → raises ReplayCodeDriftError(CODE_DRIFT)
- if stored_weights_snap != live → raises ReplayCodeDriftError(WEIGHTS_DRIFT)
- Direction thresholds mirror scheduler Stage 6: call>=put AND >=55 AND margin>=10
- NULL-safe matches: stored_call/put/direction = NULL → match=None, full_match=False

## Sentinel randomization
- C14 and C19 use random.uniform(0.10, 0.89) sentinel with assert sentinel != live D12
- Combined hash: sha256(source + '\x00' + json.dumps(weights, sort_keys=True))

## SEQ durability (R4.1/R4.2 + R4.7.2 correction)
- Era 1 (SEQ=1,2): /tmp/portfolio_engine_verify_seq — resets on VM restart (commit 339cce1)
- Era 2 (SEQ=3): tools/verified_run_last.log SCRIPT_DIR (commit 333c964) — durable, derived
- Era 3 (SEQ=4+): tools/verified_run_seq (workspace-durable; commit 9d3b41a)
- CORRECTION: "prior to SEQ=3 used /tmp" was wrong — era-2 (SEQ=3) used LOG_FILE (durable)
- Authoritative ordering: TS_END (UTC) from run log

## Log archival gap (R4.9.2)
- verified_run.sh uses tee WITHOUT -a; LOG_FILE = tools/verified_run_last.log is OVERWRITTEN each run
- Only the most recent run's log survives; historical logs are gone
- evidence_ref scheme (SEQ=N sha256=log) is unverifiable for any run except the latest
- PROPOSED fix: per-SEQ log (logs/verified_run_<SEQ>.log) + append-only index.tsv (not yet implemented)

## Hash scheme era-incompatibility (R4.7/R4.8)
- Commit 1307531 (09:07Z): combined_hash = sha256(source_only)
- Commit b733b9e (09:27Z): combined_hash = sha256(source + '\x00' + json.dumps(weights))
  — weights entered combined_hash in b733b9e; b733b9e only diff to pipeline.py = D12 0.02→0.99
- WEIGHTS_DRIFT is structurally unreachable for any row captured before b733b9e (09:27Z)

## AST gate blindness to weights (R4.9.1 — BLOCKING)
- sha256(AST(compute_req6_score)) is IDENTICAL at D12=0.02 and D12=0.99
  (68e0bf8941fc4c16376287f2429458400963ac3b64446e39fba214e2c52dee42 both values)
- The R4.8.5 gate proposal (AST-only) would NOT have tripped on the real D12 incident
- REVISED gate formula: sha256(ast.dump(ast.parse(src)) + "\x00" + json.dumps(_REQ6_SCORING_WEIGHTS))
- Only _REQ6_SCORING_WEIGHTS is the relevant module-level constant (line 299: weights = _REQ6_SCORING_WEIGHTS)

## Input hash call sites (R4.9.7)
- TRADE input_data:    5 keys: ticker, trace_id, call_score, put_score, direction
- NO_TRADE input_data: 4 keys: ticker, trace_id, call_score, put_score (NO direction key)
- Preimage 0d481fee:   3 keys: call_score, put_score, ticker — incompatible with both call sites

## Primary non-production proof (R4.9.7 corrected framing)
- Primary: all 8 rows have created_at < wiring commit d9d6987e (2026-07-19T15:16:45Z)
  — physically impossible to come from scheduler before capture_replay_inputs was wired
- Corroboration: 3-key preimage incompatible with both TRADE (5-key) and NO_TRADE (4-key)

## verified_run.sh canonical hashes
- BEFORE R4.9.5: 467451910cf5a59869fa88bd090556e5d7a7a209cc3d01d7706d27da28a0f0ae
- AFTER R4.9.5:  597862e1c39e507251dc57a4f50499909a7797c51b16e0e2769057cb040ca9c1  ← CURRENT CANONICAL
- verify_chain.sh: ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  (UNCHANGED)
- R4.9.5 patch: adds scoring_fn_ast_hash and req6_weights_hash to header (captures uncommitted states)

## reason_code assignments (R4.9.4)
- ee74327806: ERA_INCOMPATIBLE_HASH (source unchanged; scheme changed in b733b9e)
- 64d956c7ee: ERA_INCOMPATIBLE_HASH (scheme change is evidenced; source from uncommitted state → unrecoverable)
  SOURCE_CHANGED cannot be assigned: we have no committed version producing 0bc8dd98

## oe_known_synthetic_rows schema
- Columns: decision_id (PK TEXT), reason (TEXT NOT NULL), registered_at (TIMESTAMPTZ DEFAULT NOW())
- NO created_at column; ORDER BY must use registered_at
- reason strings NOT consumed as evidence in any C-check pass/fail logic
- Three rows (ee74327806/90ab047a/64d956c7ee) say "ticker=P3TEST_A" — INACCURATE (actual=P3TEST_PROD)

## SEQ run log
- SEQ=12 sha256=6cabafcc8d04d6178f50a9cb5e7f786a0c66003872d631abce1f905d3a5fc112
- SEQ=13 sha256=91a975054db1da96e20799605c7e79b9c0745018cb57b2a771337ceec6a8613e
- SEQ=14 sha256=baac6e1fc39945362d18ee2dba2d6e0c25adb96c634d92ea15a0c71ad647280f

## Scheduler cron
- seed: CronTrigger(day_of_week="mon-fri", hour=9, minute=40)
- execute: CronTrigger(day_of_week="mon-fri", hour=9, minute=45)
- Next fire: 2026-07-20T09:45:00-04:00 (Monday)

## Open proposals (no implementation except R4.9.5)
1. oe_synthetic_row_corrections — errata for immutable reason text (R4.8.1)
2. oe_unreplayable_rows — hardened with trigger/CHECK/evidence_ref NOT NULL (R4.8.4)
3. Per-SEQ log archival — logs/verified_run_<SEQ>.log + index.tsv (R4.9.2)
4. C27 end-to-end evidence_ref check (R4.9.3)
5. C28 self-approval gate check (R4.9.6)
6. oe_gate_events — suppressed-trade audit table (R4.9.6)
7. Revised engine integrity pre-flight gate: norm_hash = sha256(AST + "\x00" + weights) (R4.9.1)

## C16 fix (R5.3)
- New design: C16 uses _C16_KNOWN_FALSE="ee74327806f841a7a4034dcc" (no new FALSE rows per run)

## Replay wiring in scheduler (R5.4)
- Both TRADE (~line 1961) and NO_TRADE (~line 1729) paths in aiem_options_scheduler.py
- COMMENT IN CODE: "POST-DECISION DETECTOR ONLY — NOT a pre-trade gate."

## verification_status values
- VERIFIED, PENDING, TAMPERED, CODE_DRIFT, WEIGHTS_DRIFT, REPLAY_ERROR

## oe_criterion1_exclusions
- 1 row: 2d03987f38c44c0bbb2daa73 (R7.2 neg-control row; created 16:04Z; unregisterable by cutoff)

## PROTOCOL violation (Round 3 — logged)
- R3.2: aiem_options_pipeline.py mutated D12 0.02→0.99 live; one row captured at D12=0.99 (RETAINED)
