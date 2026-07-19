---
name: DPL Phase 3 — Reproducibility Replay & Final Remediation
description: Complete state of DPL Phase 3 after strict follow-up directive — SEQ=25, 175 PASS 1 FAIL, 4 external blockers
---

## Current state (SEQ=25)

**Chain:** 12 entries: SEQ 0 (GENESIS), 15–25. SEQ 1–14 not reconstructed.
**Verifier:** dpl/verify_dpl_phase3.py — C01–C53 (175 PASS, 1 FAIL)
**PSV:** 9/9 PASS for SEQ=25 (post_seal_verify.sh requires 4 args: SEQ CHAIN_FILE INDEX_FILE LOGS_DIR)
**Final report:** dpl/dpl_phase3_final_authoritative_report.txt (authoritative, schema v2)
**Disposition:** PASS WITH EXTERNAL BLOCKERS / DPL PRODUCTION CERTIFICATION: NOT APPROVED

## 1 Honest Failure

**C52_replay_full_match** — FAIL (correctly) — EXTERNAL_BLOCKER / PENDING_MARKET_DAY  
Prod oe_decision_replay_inputs rows were created by verifier C06 test fixtures with hardcoded scores (55.0/45.0 LONG_CALL). replay_decision() returns 39.3/39.3 NO_TRADE because stock_data_replay is incomplete.  
**Why:** No real market-day pipeline decision exists yet. Unblocks automatically when options-pipeline-scheduler runs on Mon–Fri 9:45 AM ET.

## 4 External Blockers

1. **Independent crypto approval** — Requires separate signing principal/key (ECDSA). Not implemented. C48 correctly marks as EXTERNAL_BLOCKER.
2. **Current E2E replay** — C52 FAIL (see above). Unblocks on first live market-day run.
3. **Low-privilege DB role** — Replit PG is always `postgres` (superuser). aiem_app role exists (C29 PASS) but scheduler uses owner. Triggers protect regardless.
4. **Crash-consistency process kill** — Requires isolated test environment. C35/C41 PASS for code-level recovery evidence.

## Alert_id=25 classification

LEGACY_UNREPLAYABLE — registered in oe_legacy_decision_cutoff (cutoff_id=1).  
alert_created_at=2026-07-17T14:17Z < enforcement_activation_at=2026-07-19T09:04Z (delta=~44h).  
Immutability + post-enforcement-block triggers both PASS (C47).

## SEQ=22 formal classification

INCOMPLETE_COMMAND_CAPTURE — registered in tools/defective_runs_registry.json.  
Root cause: CMD=${1} captured only first word; corrected to CMD=${*} in SEQ=23.  
SEQ=22 preserved in chain (immutable). Clean sealed runs: [23, 24, 25].

## New files (Phase 3 final)

- `tools/defective_runs_registry.json` — formal SEQ=22 classification
- `tools/chain_gap_explanation.json` — updated with correct wording (no SEQ 1–14 retroactive claim)
- `dpl/engine_integrity_refs.json` — approval_proof_status=EXTERNAL_BLOCKER, metadata_only=True
- `dpl/dpl_phase3_final_authoritative_report.txt` — single authoritative report (schema v2)
- DB: `oe_legacy_decision_cutoff` table + 2 triggers

## Chain head

SEQ=25  entry_hash=7324afdbdcab28f1568f8496282ba9e2e310b98842109605031d886f252b72a7
archive_sha256=2d591bfc35fcda8554e9f84e83f852b80c738750092b36295b255f94cece3a3b
