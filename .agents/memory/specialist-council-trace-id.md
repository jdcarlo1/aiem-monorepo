---
name: Specialist Council trace_id propagation
description: How council_run_id → trace_id backfill works; why pick-phase council precedes D2 trace minting
---

## The Architectural Constraint
`specialist_council.run_council()` fires inside `_aiem_paper_pick_candidates()`
**before** any `_d2_trace_id` exists. The D2 trace is minted per-candidate in
`_aiem_paper_execute_today()` only after the top picks are selected. Direct
pass-through is impossible.

## Fix Applied (main.py, 3 edits)
1. **~L45849** — after pick-phase `run_council()`, store council_run_id in the pick dict:
   `_sp["_council_run_id"] = _council.get("council_run_id")`

2. **~L18063-18079** — after `_d2_trace_id` is minted, backfill the council row:
   ```python
   UPDATE aiem_specialist_council_runs
   SET trace_id=%s WHERE id=%s AND trace_id IS NULL
   ```
   This means only picks that reach the **execution loop** (i.e., final top-N
   candidates) get a trace_id. Candidates filtered before that point correctly
   remain NULL — there is no D2 trace for them.

3. **~L46966** — MTM council call now passes `trace_id=f"mtm_{_id}_{_today}"`
   directly (paper_trade_id is available in that loop scope).

## Why edit 2 is a backfill not a direct pass
The council in `_aiem_paper_pick_candidates()` scores ALL preliminary candidates.
`_d2_trace_id` is only minted for candidates that survive into `_aiem_paper_execute_today()`.
The backfill UPDATE fires per surviving pick, so the 1:1 relationship is exact.

## Proof (live July 15 data)
- 9 council runs backfilled using the same UPDATE SQL from Edit 2
- Full join on `aiem_2026_07_15_BMGL_a271e7`:
  - council=1 row (wv=0.6926, signal_engine+fred_macro)
  - D2 audit=23 stages, all PASS, SHA-256 chain ✅ (stage 1-23 intact)
  - G2 ALLOW + G3 ALLOW (decision_hash cryptographically recorded)
  - paper_trade id=24 (BMGL, entry=6.4521, audit_trace_id matches)

## What still has NULL trace_id (correct)
- Council runs where ALL candidates were filtered (NO_CANDIDATES execution)
- Candidates that score high enough for council but below final cut-off
- MTM runs before this fix (have NULL; new ones use mtm_{id}_{date})
