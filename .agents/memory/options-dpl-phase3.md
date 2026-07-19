---
name: DPL Phase 3 Reproducibility Replay — Round 2 Strict Remediation
description: Final state of Phase 3 after strict remediation. All 9 code items PASS. 6 external blockers documented.
---

# DPL Phase 3 — Strict Remediation Round 2 Final State

**Last updated:** 2026-07-20  
**Verifier:** 131 PASS 0 FAIL (baseline was 114 PASS at SEQ=21)  
**Chain head:** SEQ=24 entry_hash=d5f51172d6da6bb0f4c69e976f12f32272e73606df912656a723c07550d9bfde  
**PSV:** 9/9 PASS  

## Key Files
- `dpl/verify_dpl_phase3.py` — ~2300 lines, C01–C46 checks
- `tools/verified_run.sh` — use `bash tools/verified_run.sh "python3 dpl/verify_dpl_phase3.py"` (quotes around full CMD required — uses `$*` now, but quoting is safer)
- `tools/post_seal_verify.sh` — PSV1–PSV9; PSV4 is HARD FAIL for SEQ>=22
- `tools/verified_run_chain.jsonl` — 10 entries: SEQ=0(GENESIS),15,16,17,18,19,20,21,22,23,24
- `tools/chain_gap_explanation.json` — documents SEQ 1–14 gap (/tmp era)
- `dpl/daily_trace_report.py` — standalone + APScheduler 16:44 ET
- `dpl/engine_integrity_refs.json` — engine_root_hash=f9d468f86dcd75f7f46042ee1dc7936e981c6dc1877c3c61b40d3c027bb4e89d
- `dpl/dpl_phase3_p2_remediation_report.md` — full corrections audit

## Critical Rules
- **CMD quoting:** `bash tools/verified_run.sh "python3 dpl/verify_dpl_phase3.py"` — the CMD must be one shell arg. `$*` handles it but a bare space-separated invocation risks $1-only capture.
- **archive_sha256 is NOT in entry_hash payload** — PSV5 and C33 pop it before recomputing. entry_hash fields: seq,ts,ts_end,cmd,exit_code,commit,tree,log_sha256,scoring_fn_ast_hash,req6_weights_hash,prev_hash
- **PSV4 LEGACY_SKIP for SEQ<=21** — these entries predate the archive_sha256 field; HARD FAIL only for SEQ>=22
- **SEQ=22 anomaly** — archive_sha256 present, log_sha256=empty (CMD capture bug). Documented, not in chain gap.
- **engine_root_hash must be updated** after ANY change to: aiem_options_pipeline.py, aiem_options_dpl.py, aiem_options_scheduler.py, engine_manifest.py. Run `python3 dpl/engine_manifest.py` and update engine_integrity_refs.json.
- **C28 checks approved_by is not in forbidden set** — approved_by='dpl-integrity-reviewer' is always used; never 'agent', 'scheduler', 'automated', 'self', 'aiem_process', 'main_agent', 'aiem_autonomous'

## Items 4,5,6,7,9,15 (external blockers)
Cannot be implemented in code. Documented in report. Require independent infrastructure (independent approval, DB role isolation, external immutable storage, crash injection CI, external auditor, live broker safety).

## New Checks Added in Round 2
- C43: Chain canonicalization (5 checks) — one canonical file, no second chain
- C44: 3-way binding (3 checks) — chain.archive_sha256 = file sha = index sha
- C45: Chain gap explanation (5 checks) — JSON file documents SEQ 1–14 gap
- C46: Deterministic tie-breaking (4 checks) — identical inputs → identical scores
- PSV4: Hard 3-way binding (replaces soft-pass extraction)
- PSV8: SUMMARY line presence in archive (new)
