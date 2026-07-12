---
name: D3 PAPER ENFORCEMENT design
description: Design decisions for transitioning G0 from VERIFIED SHADOW to PAPER ENFORCEMENT — where the halt happens, what changes, and the paired BLOCK/ALLOW test design.
---

## The rule
PAPER ENFORCEMENT requires NO code changes. The G0 enforcement path is already fully implemented and verified (D5/D6). The only operational change needed is a DB config update: `mode=SHADOW → mode=ENFORCE` in the governance config table.

**Why:** G0's BLOCK path (main.py lines 43079–43112) already short-circuits `_aiem_paper_execute_today()` before any trade-executing code runs. The path releases the lock, writes the exec_log row, acks the decision, and returns `{"blocked": True, ...}`. The `INSERT INTO aiem_paper_trades` is at line 44227 — unreachable on a BLOCK.

## Pre-ENFORCE guards (must be implemented before mode switch, not just documented)

### Guard A — State-NORMAL check before mode switch
Before updating the DB config to ENFORCE, a runtime check must confirm
`governance_state = NORMAL`. If the state is anything else (PAUSED, RESTRICTED,
RECOVERY_REQUIRED, ROLLBACK_IN_PROGRESS), the ENFORCE switch must be blocked and
an explicit, logged reason must be recorded — the switch cannot proceed silently.

Implementation: a pre-switch validation function (or admin endpoint step) that:
1. Reads the current governance state from the DB config table
2. If state != NORMAL: prints/logs a clear error, writes a governance event
   (`enforce_switch_blocked, reason=state_not_normal`), and refuses to update the
   mode column
3. Only if state == NORMAL: proceeds with the UPDATE and logs
   `enforce_switch_completed, previous_mode=SHADOW, new_mode=ENFORCE`

### Guard B — BLOCKED_G0 alerting path
When `status='BLOCKED_G0'` is written to `aiem_paper_execution_log`, a Telegram
notification must fire to the owner. Silent halts are not acceptable in ENFORCE mode.

Implementation: in the BLOCK path of `_aiem_paper_execute_today()`, immediately
after the exec_log INSERT commits, call `_tg_send()` with a message of the form:

  🚨 [D3-G0 BLOCK] Paper trading halted by governance — no trades placed today.
  State: {system_state} | Mode: {checkpoint_mode} | Trigger: {trigger_source}
  Decision ID: {governance_decision_id}

Non-fatal: the Telegram call must be wrapped in try/except so a delivery failure
never affects the BLOCK path's lock release or return value.

## How to apply
1. Implement Guard A (state-NORMAL check) and Guard B (Telegram alert) — see above
2. Run Guard A's check: confirm governance state = NORMAL
3. Update DB config: `UPDATE aiem_g0_config SET mode='ENFORCE' WHERE ...`
4. Verify Guard B fires on the next BLOCKED_G0 event (canary test)

## Paired BLOCK/ALLOW test design (for the enforcement verification directive)
- Real production DB URL (not d3_test_isolation schema)
- Temporarily set DB config to ENFORCE+PAUSED
- Mock `_aiem_paper_pick_candidates()` → 1 candidate
- Mock `_is_trading_day` → True
- Assert: paper_trades count unchanged, exec_log has BLOCKED_G0 row, return["blocked"]=True
- Control: set DB config back to SHADOW/ALLOW, same call → paper_trades count +1
- Cleanup: at the time of cleanup, state exactly which test row(s) are to be deleted
  and why, then wait for explicit approval in that session before deleting anything.
  No row may be deleted without that session-specific approval.

## Blast radius
- Zero effect on live brokerage execution (doesn't exist yet)
- G0 is only called from `_aiem_paper_execute_today()` — no other function is affected
- D1 scheduling, scanners, MTM, research tabs all unaffected
