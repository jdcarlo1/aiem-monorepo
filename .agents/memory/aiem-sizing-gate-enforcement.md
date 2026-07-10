---
name: AIEM sizing gate enforcement fix
description: Fail-closed sizing gate in _aiem_paper_execute_today; surfaced that 9/10 signal sources have no real stop-loss logic and are now blocked from trading
---

# AIEM Sizing Gate Enforcement Fix

**The bug (pre-2026-07-10):** `_aiem_paper_execute_today()` in main.py called
`aiem_position_sizing.compute_position_size()`, but any non-`APPROVED` `gate_result`
(including a raised exception) only printed a warning and still inserted the trade
at a default $1000 notional. The gate existed in code but never actually blocked
anything.

**The fix:** allowlist only `("APPROVED", "PARAMS_NOT_CONFIRMED")` proceed to trade
insertion; every other `gate_result` (`kill_switch`, `max_positions`,
`max_sector_positions`, `daily_loss`, `CONVICTION_BELOW_MIN`, `NO_STOP_DEFINED`,
`STOP_UNDEFINED`, `POSITION_TOO_SMALL`, `SIZING_ERROR`, or any future/unknown value)
now `continue`s past that candidate with zero trade insertion and zero default
notional fallback.

## Why: `_STOP_REGISTRY` only covers 1 of 10 sources
`aiem_position_sizing.py`'s `_STOP_REGISTRY` (~line 262) has a real stop-derivation
function for exactly one signal source: `Oversold_Bounce_Uptrend`. All other active
paper-trading sources (`gap_volume`, `aiem_ai`, `multi_signal`, `unusual_calls`,
`aiem_v3_discovery`, `conviction_stack`, `sweep`, `oi_buildup`, `layer9_stat`,
`washout_ignition`) map to `None` or aren't in the dict — `derive_stop()` returns
`NO_INVALIDATION_POINT_DEFINED_FOR_SOURCE` for all of them. This is intentional
fail-closed design per the code's own comment ("no fallback to generic % — if
source not here, trade is skipped"), NOT a bug in the fix.

**Practical consequence:** with the fix live, every active source except
`Oversold_Bounce_Uptrend` is fully blocked from opening new paper trades until a
real stop-derivation function is written for it. Verified live 2026-07-10:
force-execute produced 12 real candidates, all blocked `NO_STOP_DEFINED`, 0 trades
inserted (`aiem_paper_trades` max id unchanged), all 12 durably logged to
`aiem_position_sizing_log`.

## How to Apply
- Before assuming AIEM paper trading is "not firing" due to a bug, check
  `aiem_position_sizing_log.gate_result` first — `NO_STOP_DEFINED` for a given
  source means no thesis-based stop function exists yet, not a broken pipeline.
- To re-enable a source, add a real stop-derivation function to `_STOP_REGISTRY`
  following the pattern of `_stop_oversold_bounce`, then verify via a forced
  test run that the source can reach `APPROVED`.
- Testing the sizing gate live requires a real candidate to reach the sizing step;
  the pre-existing portfolio-concentration-risk gate can block ALL candidates
  upstream if positions are already correlated/at cap — may require manually
  closing a blocking position (`status='CLOSED_MANUAL_ADMIN'`, real Tradier quote)
  to get a clean test run.
