---
name: DPL Phase 3 — Reproducibility Replay
description: oe_decision_replay_inputs table + replay_decision() + 26-check verifier; SEQ=13 EXIT=0; Criterion 1 BLOCKED; real-decision pending Mon 2026-07-21 09:45 ET
---

# DPL Phase 3 — Reproducibility Replay

## Status
Implementation: COMPLETE (R5.1–R5.4 done; C01–C26 PASS SEQ=13 EXIT=0)
Criterion 1 (real scheduler decision replayed): BLOCKED
Real-decision replay evidence: PENDING Monday 2026-07-21 09:45 ET
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
- Eliminates hardcoded 0.99 sentinel that would collide with a deliberate D12=0.99 config
- Combined hash: sha256(source + '\x00' + json.dumps(weights, sort_keys=True))

## SEQ durability (R4.1/R4.2)
- Era 1 (SEQ=1,2): /tmp/portfolio_engine_verify_seq — resets on VM restart (commit 339cce1 2026-07-19T04:30Z)
- Era 2 (SEQ=3): verified_run_last.log at SCRIPT_DIR (commit 333c964 2026-07-19T14:43Z) — durable, derived
- Era 3 (SEQ=4+): tools/verified_run_seq (workspace-durable; commit 9d3b41a 2026-07-19T14:54Z)
- CORRECTION: "prior to SEQ=3 used /tmp" was wrong — era-2 (SEQ=3) used LOG_FILE (durable, not /tmp)
- Authoritative ordering: TS_END (UTC) from run log

## Hash scheme era-incompatibility (R4.7/R4.8)
- Commit 1307531 (09:07Z): combined_hash = sha256(source_only)
- Commit b733b9e (09:27Z): combined_hash = sha256(source + '\x00' + json.dumps(weights))
  — weights entered combined_hash in b733b9e; b733b9e only diff to pipeline.py = D12 0.02→0.99
- WEIGHTS_DRIFT is structurally unreachable for any row captured before b733b9e (09:27Z).
  The hash scheme change makes stored vs live hashes era-incompatible; CODE_DRIFT fires first.
- stored for ee74327806: 4fbe78c9... (source-only) = current source hash (function unchanged at HEAD)
- stored for 64d956c7ee: 0bc8dd98... (source-only) = NO committed version; uncommitted state at 09:24Z

## input_hash preimage (R4.7.1 / R4.8.2)
- All 8 is_test_record=FALSE rows share hash: 0d481fee5ddb7771cda787c0ea60d8240e1579fe3299126a9d91e1d8e50143cf
- Preimage: json.dumps({"call_score": 60.0, "put_score": 50.0, "ticker": "P3TEST_PROD"}, sort_keys=True)
- 3 keys only; oe_known_synthetic_rows reason says "P3TEST_A" for ee74327806/90ab047a/64d956c7ee — INACCURATE
- Scheduler TRADE path uses 5 keys: ticker, trace_id, call_score, put_score, direction
- A 5-key dict cannot produce 0d481fee (structurally incompatible JSON; SHA-256 collision-resistant)
  — 60-combination scan confirms no collision; this is primary non-text proof of non-production origin

## oe_known_synthetic_rows errata (R4.8.1)
- Table has no created_at column; ORDER BY must use registered_at
- 10 rows total:
  1/2. 972f0ffe / 1f436a10 — C06-C08 trigger-test
  3/4. ee74327806 / 90ab047a — R3 dev manual; reason says "ticker=P3TEST_A" — INACCURATE (actual=P3TEST_PROD)
  5.   64d956c7ee — R3 mutation window D12=0.99; reason says "ticker=P3TEST_A" — INACCURATE (actual=P3TEST_PROD)
  6-8. 43fc85d578 / 9d54962e / ef3a765a / d73cc2f1 / e391103d — C16 SEQ=1-5 old design flaw
  (10th: 2d03987f — R7.2 cutoff neg-control in oe_criterion1_exclusions, not oe_known_synthetic_rows)
- reason strings NOT consumed as evidence in any verifier C-check pass/fail logic
  (only used at line 790 as C21 mutation-probe target and at line 932 for INSERT schema)

## Proposed errata table (R4.8.1 — not yet implemented)
oe_synthetic_row_corrections(correction_id SERIAL PK, decision_id FK, corrected_field TEXT,
  asserted_value TEXT, actual_value TEXT, evidence_ref TEXT NOT NULL,
  corrected_at TIMESTAMPTZ DEFAULT NOW(), corrected_by TEXT NOT NULL)
+ BEFORE UPDATE OR DELETE immutability trigger

## Proposed oe_unreplayable_rows (R4.8.4 — not yet implemented)
Columns: decision_id PK FK, registered_at TIMESTAMPTZ, reason TEXT NOT NULL,
  reason_code TEXT CHECK IN ('ERA_INCOMPATIBLE_HASH','SOURCE_CHANGED','WEIGHTS_CHANGED','SCHEMA_MISMATCH'),
  evidence_ref TEXT NOT NULL (format: "SEQ=N sha256=<log>"),
  registered_by TEXT NOT NULL CHECK IN ('verify_dpl_phase3.py','admin_manual_with_evidence')
+ BEFORE UPDATE OR DELETE immutability trigger
Registration precondition: replay_decision() must raise in SAME verifier run; evidence_ref must
reference that run's SEQ+sha256. Suppression prevention: evidence_ref NOT NULL + immutability
trigger + C27 calls replay_decision() live and only exempts registered rows.

## AST normalization for pre-flight gate (R4.8.5)
ast.dump(ast.parse(source)) absorbs: comments, trailing whitespace (single-line), blank lines
ast.dump() still trips on: variable renames, parameter renames, docstrings (Constant nodes),
  multi-line string literal modifications (string content is in Constant nodes)
Pre-flight gate: compare sha256(ast.dump(ast.parse(fn_src))) against published reference constant
in dpl/engine_integrity_refs.json; fire BEFORE save_options_alert() at line 1847;
re-approval path = update reference file with new hash + approved_by + commit SHA

## TREE=DIRTY and git diff HEAD --stat
- Every verified_run.sh run increments tools/verified_run_seq → always shows in git diff HEAD --stat
- SEQ=12 R4.7 response showed file-specific diff (-- README.md only) not global --stat
- SEQ=13 log shows verified_run_seq | 2 +- explicitly (git diff HEAD --stat inside script)

## D12 restoration (Round 3 — A1)
- D12=0.02 restored to aiem_options_pipeline.py

## oe_known_synthetic_rows schema
- Columns: decision_id (PK TEXT), reason (TEXT NOT NULL), registered_at (TIMESTAMPTZ DEFAULT NOW())
- NO created_at column; ORDER BY must use registered_at

## C16 fix (R5.3)
- New design: C16 uses _C16_KNOWN_FALSE="ee74327806f841a7a4034dcc" (no new FALSE rows per run)

## Replay wiring in scheduler (R5.4)
- Both TRADE (~line 1961) and NO_TRADE (~line 1729) paths in aiem_options_scheduler.py
- COMMENT IN CODE: "POST-DECISION DETECTOR ONLY — NOT a pre-trade gate."
- On ReplayCodeDriftError: log.critical + _tg() + UPDATE oe_decision_audit SET verification_status

## Round 6 additions
- 10 oe_known_synthetic_rows total; 0 Criterion 1 eligible rows
- trg_oe_known_synthetic_immutable BEFORE UPDATE OR DELETE confirmed
- verification_status check constraint: VERIFIED, PENDING, TAMPERED, CODE_DRIFT, WEIGHTS_DRIFT, REPLAY_ERROR

## Round 7 changes
- oe_decision_audit check constraint: REPLAY_ERROR added
- T3/NT3 scheduler paths: log.critical + _tg() + UPDATE verification_status='REPLAY_ERROR'
- WEIGHTS_DRIFT dynamic status in T2/NT2: "WEIGHTS_DRIFT" if in str(_rce) else "CODE_DRIFT"
- trg_oe_known_synthetic_cutoff blocks registration of post-wiring-cutoff decision_ids
- R7.2 neg-control row 2d03987f38c44c0bbb2daa73 is permanent FALSE row (unregisterable by cutoff)

## Round 8 changes
- oe_criterion1_exclusions table + immutability trigger; 2d03987f38c44c0bbb2daa73 registered
- C22/C24/C25/C26 added; SEQ=10 31 PASS 0 FAIL

## Canonical file sha256s (SEQ=13 era)
- tools/verified_run.sh:  467451910cf5a59869fa88bd090556e5d7a7a209cc3d01d7706d27da28a0f0ae  ← CANONICAL
- verify_chain.sh:        ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  ← CANONICAL
- dpl/README.md:          38ffd3b7cdd46dc6b0b22ba32a39e7ead2af14b560ffc0fc4147c82cc445e003  (after R4.7.2 fix)

## PROTOCOL violation (Round 3 — logged, not self-corrected)
- R3.2: aiem_options_pipeline.py mutated D12 0.02→0.99 while scheduler was live
- One production row captured at D12=0.99 (decision_id=64d956c7…) — RETAINED per immutability rule
