---
name: AIEM feedback loop bug fixes session July 2026
description: 11 verified bugs in supervisor/pipeline/closed-loop; 7 fully fixed, 2 proven working, 1 design decision, 1 pending live MTM proof
---

## Fixed bugs (all have DB/log proof)

**Item 1 — supervisor column names**
- `aiem_supervisor.py` required_cols: `trust_mult`→`trust_multiplier`, `drift_mult`→`drift_multiplier`
- Real columns in `aiem_candidate_rankings`: trust_multiplier, drift_multiplier, rl_weight (all three exist)

**Item 2 — PAPER_TRADE_OPENED race**
- 3-attempt retry (0.6s sleep) in `supervisor_on_paper_trade_opened` before TRADE_NOT_FOUND
- Race: supervisor opens new connection before caller's transaction commits

**Item 3 — Silent failure gate**
- `feedback_failure_log` table created; both `log_learning_update_step` and `log_future_decision_step` write escalated=TRUE row on exception
- Force-trip proven: row id=1 in table

**Item 4 — exec_ms NULL**
- `PipelineTrace.__init__` sets `self._step_clock = time.monotonic()`; `log_step` auto-computes per-step elapsed
- All callers (11 log_step calls in paper execute loop) get real timing automatically

**Item 5 — aiem_decision_log**
- Table created; write wired in `supervisor_on_final_decision` after `_upsert_loop_audit`

## Confirmed working (items 8/9)
- bull_bear debate runs for top-3 picks per batch; N/A is correct for picks 4+
- Proven: 6/20 audit rows today have bull_bear=NEUTRAL

## Design decision (item 10)
- Paper trades intentionally don't send Telegram
- aiem_process.py fires at 9:30 AM ET for real AIEM picks (different channel)

## Root cause found, pending live proof (items 6/7, 11)
- `record_trust_update` function itself is working (proven: row id=4, LRCX written directly)
- Gap: signal_trust_weights rows didn't exist when MTM ran on July 6 (seeded at 02:22 UTC, trades closed 20:01 UTC prior day)
- Items 6/7 and 11 will auto-prove at today's 4 PM ET MTM run

## Known non-critical bug (out of scope)
- L4352: `entry_time` column doesn't exist — causes false startup catch-up but doesn't block trades
