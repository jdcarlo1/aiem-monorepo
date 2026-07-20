---
name: DPL Phase 3 — Reproducibility Replay & R4 Remediation
description: Complete state after R4 audit-response — SEQ=35 sealed, 177 PASS / 6 FAIL (all classified), PSV9 PASS
---

## Current state (SEQ=35, R4 COMPLETE)

**Chain:** 21 entries: SEQ 0 (GENESIS), 15–35. SEQ 1–14 not reconstructed.
**Verifier:** dpl/verify_dpl_phase3.py — C01–C54 + R4 additions (177 PASS, 6 FAIL — all correctly classified)
**PSV:** 9/9 PASS for SEQ=35
**R4 response:** dpl/DPL_Phase3_R4_Response.txt (read-and-report items A7/A8/A9/B12)

## 6 Remaining Failures (all correctly classified)

| Check | Classification | Notes |
|---|---|---|
| C48_independent_approval_obtained | EXTERNAL_BLOCKER | approved_at/approved_by null; allowlist empty |
| C28_approved_by_in_allowlist_and_engine_hash_match | EXTERNAL_BLOCKER | allowlist=set() — no external reviewer |
| C52A_verifier_fixtures_contaminate_prod_namespace | IMPLEMENTATION_DEFECT | 9 rows documented in contamination_registry.json |
| C52B_scheduler_origin_decision_exists | PENDING | Unblocks Mon–Fri 9:45 AM ET (any market day) |
| C52B_live_trade_decision_exists | PENDING_LIVE_EVIDENCE | Unblocks on a TRADE market day |
| C52C_genuine_replay_pass | DEPENDENCY_BLOCKED | Blocked by C52B |

## R4 Code Changes Applied (SEQ=35)

**C2 — C48 collapsed:** 7 granular C48 checks → single `C48_independent_approval_obtained`.
FAIL when approved_at=None OR approved_by=None. All 7 superseded in A8 registry.

**A4 — C52_replay_returns_structure non-tautological:**
- Fixture patched with `scoring_fn_combined_hash` from `engine_integrity_refs.json`
  (sha256(getsource + "\x00" + weights_json) — same type replay_decision uses; NOT live getsource).
- Added `C52_replay_code_drift_raises_error` negative control.
- Critical implementation note: negative control must COMMIT wrong hash before calling
  replay_decision (NOT use SAVEPOINT) because replay_decision opens its own DB connection
  and only sees committed data. SAVEPOINT-only leaves wrong hash invisible to replay_decision.

**A5 — C28 blocklist → allowlist:**
- `_C28_APPROVED_IDENTITIES = set()` (fail-closed, empty = no reviewer).
- Supersedes `C28_approved_by_null_or_not_forbidden`.
- Gate also requires engine_root_hash runtime match.

**C52B FORK split:**
- `C52B_scheduler_origin_decision_exists` — alert_id may be null (unblocks any market day).
- `C52B_live_trade_decision_exists` — alert_id IS NOT NULL (TRADE day only).
- Supersedes `C52B_genuine_scheduler_decision_exists`.

**A8 Enforcement:** `_A8_SUPERSEDE_REGISTRY` dict at end of verifier; 9 new entries from R4.
Violation = FAIL appended to _FAIL list. 0 violations at SEQ=35.

**Certification text:** printed before SUMMARY; updates based on C52B PASS status.

**B5 — excluded_from column dropped** from `oe_contamination_exclusions` (vestigial).

## Key hash values

- `scoring_fn_combined_hash` in refs: `eb28b76efd53485602c648744c60642f87a6bb0c09ce02b0f0071ee2cfc6583a`
  (sha256(getsource(compute_req6_score) + "\x00" + json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True)))
- `scoring_fn_ast_hash` in refs: `68e0bf89...` — DIFFERENT computation (AST dump only); do not confuse
- `engine_root_hash` in refs: `4ff60253f52e37d5b1b65dbae40c56f960a835b59bab78714036b9dabb55f4b4`

## Key files

- `dpl/verify_dpl_phase3.py` — C01–C54 + R4 additions
- `dpl/engine_integrity_refs.json` — scoring_fn_combined_hash added (R4 A4)
- `dpl/contamination_registry.json` — 9 contaminated rows documented
- `dpl/DPL_Phase3_R4_Response.txt` — R4 read-and-report response
- `tools/last_run_results.json` — 177 PASS / 6 FAIL (SEQ=35)
- `tools/verified_run_chain.jsonl` — 21 entries SEQ=0,15-35

## Chain head (SEQ=35)

entry_hash=69c829416fdf0ddc266f077c77273ff29feb70fbef1ccd2e5e2838aa030dc0ab
archive_sha256=2b6e84944e7aa2674fb0eb70fe16b8d685b924079c846d3505012c095fdc86d4
ts_end=2026-07-20T03:02:45Z

## Next unblock condition

Run `bash tools/verified_run.sh` after Mon 2026-07-21 09:45 ET to check C52B_scheduler_origin_decision_exists.
TRADE market day needed for C52B_live_trade_decision_exists and C52C_genuine_replay_pass.
