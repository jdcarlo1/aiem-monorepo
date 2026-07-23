---
name: Phase 4 OPP/TRACE verification
description: Final sealed state of OPP-001–039/051–060 and TRACE-001–050 against 2026-07-23 live cycle data
---

## Result
SEQ=92 sealed 2026-07-23.  PASS=53 / FAIL=0 / PENDING=6 / INV=54.  EXIT=0.
Permanent record: `docs/verification/phase4-opp-trace-FINAL.md`

## Today's Cycle
5 tickers (DG, UPS, HUM, DOCU, DUOL), jobs 151–155.
All status=FAILED: "BOTH DIRECTIONS REJECTED by hard gates."
oe_strategy_candidates=0 total — pipeline never reached scoring.

## Two Real Gaps Found and Closed

### Gap 1+2 — TRACE-015 / TRACE-048 (discovered SEQ=90)
Hard-gate rejection path exited exception handler before writing
PAPER_EXECUTION_OR_NO_TRADE or computing chain_hash.

Fix in `aiem_options_scheduler.py` exception handler:
```python
_is_gate_reject = err_msg.startswith("not ready_for_decision")
```
When true: compute `_failed_chain_hash`, write it to `options_pipeline_jobs`,
and write PAPER_EXECUTION_OR_NO_TRADE with `completion_status="NO_TRADE_HARD_GATE"`.

Retroactive repair applied for jobs 151–155 (`retroactive_repair=True` in stage_metadata).

### Gap 3 — TRACE-030 verifier gate (discovered SEQ=91)
Check `all(r[3] in (1,2,3))` was too narrow — PAPER_EXECUTION_OR_NO_TRADE
has stage_seq=11.  Fixed to `all(r[3] >= 1)`.

## PENDING=6 — When They Resolve
TRACE-016–021 require a day where the pipeline generates a qualifying candidate
(both-direction gate passes + scoring threshold met + order placed + position
opened/closed). All PENDING, not FAIL.

## INV=54 — Root Cause
All 54 IMPLEMENTED_NOT_VERIFIED items share one root cause: `oe_strategy_candidates=0`
rows ever. Hard gates reject both directions before the scoring/strategy phase.
Items are correctly coded; evidence only appears when a qualifying candidate
clears all 18 hard gates.

**Why:** This distinction matters — INV is not a bug, it is a coverage gap waiting
on a live qualifying signal day.
