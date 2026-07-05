---
name: Module B Short Squeeze Reversion
description: Backtest results, wiring, and honest NOT_IMPLEMENTED status for the Short Squeeze Reversion signal
---

## Signal definition
rvol >= 3.0, close_strength >= 0.65, range_pct >= 5%, prior_5d_ret <= -3%,
price $3-$200, volume >= 300k. Proxy-only (no SI%, borrow cost, or DTC).

## Backtest result (polygon_market_daily 2026-04-07 to 2026-06-23)
- total_n: 251, gate_passed: 138, suppressed: 113 (all falling_knife: prior_5d_ret <= -20%)
- WR_3d: 40.6%, avg_ret_3d: -1.86% — BELOW 50% baseline
- p_value: 0.9999 (i.e. not statistically significant in the positive direction)
- status: hypothesis (correctly; does not qualify for live use)
- aiem_signal_discoveries id=14

## Why it underperforms
The proxy conditions (rvol+close_strength+range_pct) likely capture "volatile bounce
days within a continuing downtrend" rather than true short-squeeze events. Without real
SI%, borrow cost, or DTC data, the signal cannot distinguish a genuine squeeze from
a dead-cat bounce.

## Live-use gate
Paper trading source 10 checks `status = 'validated'` before adding candidates — same
pattern as washout_ignition. Currently always skipped (hypothesis status, WR < 50%).

## NOT_IMPLEMENTED fields (documented in every DB row)
- borrow_cost_status: no borrow-cost feed in any table or API
- si_pct_status: Finviz SI% is live-only; no historical data in DB
- dtc_status: days-to-cover not available anywhere

## B.1 contrarian-indicator audit (aiem_position_sizing.py)
- rsi_14 bug fixed: polygon_market_daily has NO rsi_14 column; now uses close_strength < 0.20
- put/call (CBOE 403), fear/greed (CNN 418), AAII (auth-required), NAAIM (xlsx 404)
  all documented as NOT_AVAILABLE with exact failure reasons in docstring

**Why:** Protocol requires honest NOT_IMPLEMENTED/NOT_AVAILABLE labelling, never silent omission.
**How to apply:** Any future signal using missing data must label it NOT_IMPLEMENTED; any backtest
using a proxy must say so in the column notes.
