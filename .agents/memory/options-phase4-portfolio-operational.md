---
name: Options Engine Phase 4 — Portfolio & Operational Learning
description: Sections 15-17 of the AIEM Standalone Options Engine Phase III directive; tables, guard invariants, wiring points, and known design constraints.
---

# Options Engine Phase 4 — Portfolio & Operational Learning

## Tables (all prefixed oe_)
- `oe_portfolio_context` — one row per pipeline run (UNIQUE on trace_id only; alert_id nullable, allows multiple rows per alert from pre/post capture and backfill)
- `oe_no_trade_candidates` — one row per NO_TRADE decision (UNIQUE on job_id)
- `oe_incidents` — one row per operational failure (UNIQUE on failure_source+reference_id+failure_type)

## Key design constraints

### Portfolio guard (Section 15) — hard invariant
`apply_portfolio_learning_guard(alert_id, pnl_pct)` queries oe_portfolio_context WHERE alert_id=%s ORDER BY created_at DESC LIMIT 1.  If violated_limits≠[] AND pnl_pct > 0 → decision_quality='BAD'. This is forced in the pipeline's Stage 9 learning_data dict before the SHA-256 hash is computed.

**Why:** A profitable trade that violated portfolio limits at entry must NOT propagate a positive learning signal. The guard is wired in aiem_options_pipeline.py before the Stage 9 hash, so violations are cryptographically locked into the audit chain.

**UNIQUE(alert_id) was removed** from oe_portfolio_context because: (1) capture is called with alert_id=None pre-decision, (2) backfill uses alert_id=real_id with trace='backfill_{id}', so two rows for same alert_id are valid. Only UNIQUE(trace_id) is enforced.

### No-Trade Learning (Section 16)
`backfill_no_trade_candidates()` backfills from options_pipeline_jobs WHERE direction='NO_TRADE'. `compute_rejection_rates()` returns statistical_claim=False when n_classified < 20. As of July 2026: 1 NO_TRADE candidate (MEC 2026-07-15, job_id=29). Outcome classification requires polygon_market_daily T+5 data.

### Incident classifier (Section 17) — deterministic
`classify_incident(error_text, failure_source)` is a pure function that pattern-matches error_text against _INCIDENT_PATTERNS list. classification is ALWAYS 'OPERATIONAL' — this module only records operational (not model) failures. MODEL errors are never stored as incidents.

The `scan_operational_failures()` function scans: job_heartbeats (last_error IS NOT NULL), options_pipeline_jobs (status='FAILED'), daily_pipeline_runs (status='FAILED' OR stranded SCHEDULED).

Real incident confirmed: 'missing Polygon/OSS data for TER 2026-07-17' → type=MISSING_DATA, cls=OPERATIONAL.

## Wiring points
1. aiem_options_scheduler._execute_job: phase4 import + bootstrap after phase3 block (~line 741)
2. aiem_options_scheduler._execute_job: `capture_portfolio_context()` before `if direction == "NO_TRADE":` block
3. aiem_options_scheduler._execute_job NO_TRADE branch: `record_no_trade_candidate()` after phase3 KB entry, before return
4. aiem_options_scheduler._execute_job error path: `record_incident()` after `_write_heartbeat(False, err_msg)`
5. aiem_options_scheduler.grade_outcomes_job: `track_no_trade_outcomes()` + `scan_operational_failures()` after phase3 block
6. aiem_options_pipeline.grade_options_outcomes: `apply_portfolio_learning_guard()` before Stage 9 hash; forces decision_quality in learning_data

## Verification
- verify_phase4.py runs through verified_run.sh (SHA=8146a523...)
- SEQ=22 EXIT=0 PASS=35 FAIL=0 (2026-07-18T21:45:43Z)
- TEST-1 PORTFOLIO_GUARD: PASS — 6 violations (23 open > 10 limit; PSX/NTLA/EW/MAA each >2x; $29299 > $20000) → guard returns BAD for pnl=10%
- TEST-2 REJECTION_RATES: PASS — n_classified=0 < 20 → statistical_claim=False, honest suppression
- TEST-3 OPERATIONAL_CLASS: PASS — TER MISSING_DATA/OPERATIONAL in oe_incidents (id=4)
- Proof file: tools/phase4_verify_seq22.txt (sha256=fc6e569d...)
