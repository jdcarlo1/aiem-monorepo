---
name: D3 PAPER ENFORCEMENT design
description: Design decisions for transitioning G0 from VERIFIED SHADOW to PAPER ENFORCEMENT — where the halt happens, what changes, and the paired BLOCK/ALLOW test design.
---

## The rule
PAPER ENFORCEMENT requires NO code changes. The G0 enforcement path is already fully implemented and verified (D5/D6). The only operational change needed is a DB config update: `mode=SHADOW → mode=ENFORCE` in the governance config table.

**Why:** G0's BLOCK path (main.py lines 43079–43112) already short-circuits `_aiem_paper_execute_today()` before any trade-executing code runs. The path releases the lock, writes the exec_log row, acks the decision, and returns `{"blocked": True, ...}`. The `INSERT INTO aiem_paper_trades` is at line 44227 — unreachable on a BLOCK.

## How to apply
1. Confirm governance state = NORMAL before switching mode to ENFORCE (state=PAUSED/RESTRICTED would immediately halt paper trading)
2. Update DB config: `UPDATE aiem_g0_config SET mode='ENFORCE' WHERE ...`
3. Set up alerting on new `aiem_paper_execution_log` rows with `status='BLOCKED_G0'`

## Paired BLOCK/ALLOW test design (for the enforcement verification directive)
- Real production DB URL (not d3_test_isolation schema)
- Temporarily set DB config to ENFORCE+PAUSED
- Mock `_aiem_paper_pick_candidates()` → 1 candidate
- Mock `_is_trading_day` → True
- Assert: paper_trades count unchanged, exec_log has BLOCKED_G0 row, return["blocked"]=True
- Control: set DB config back to SHADOW/ALLOW, same call → paper_trades count +1
- Cleanup: delete test paper trade row (pre-approved in that session)

## Blast radius
- Zero effect on live brokerage execution (doesn't exist yet)
- G0 is only called from `_aiem_paper_execute_today()` — no other function is affected
- D1 scheduling, scanners, MTM, research tabs all unaffected
