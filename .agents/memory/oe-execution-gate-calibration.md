---
name: OE execution gate calibration
description: Three compounding bugs that caused every options engine execution to fail the verify_options_decision_inputs hard gates for 10+ consecutive trading days, writing zero rows to oe_decision_records or oe_no_trade_candidates.
---

## Root cause (fixed 2026-08-01, commit 1fe78da, TLA-19cdde04)

All three bugs must be absent for a candidate to pass the hard gates in `verify_options_decision_inputs` (aiem_options_intel.py).

### Bug 1 — _sinc strike-increment formula (aiem_options_scheduler.py ~line 1590)

**Old:** `_sinc = 1.0 if spot < 5 else 2.5 if spot < 25 else 5.0`  
**Fixed:** `_sinc = 1.0 if spot < 100 else 2.5 if spot < 300 else 5.0`

With sinc=5 for any stock above $25, the formula `ceil(spot * 1.025 / 5) * 5` yielded strikes 5–12.5% OTM for stocks in the $25–$100 range. Example: HAL $40 → call_strike $45 (12.5% OTM), delta=0.02, probability_estimate=0.018. Multiple gates fired simultaneously.

**How to apply:** If OSS candidates look reasonable but 100% fail gates, check _sinc first. The intended target is always ~2.5% OTM; verify `(call_strike / spot - 1) * 100` is ≤ 4% for any real candidate.

### Bug 2 — probability_estimate gate threshold (aiem_options_intel.py ~line 449)

**Old:** `lambda v: float(v) < 0.35`  
**Fixed:** `lambda v: float(v) < 0.25`

For 2.5% OTM 9-DTE options under typical IV (0.25–0.40), N(d2) ≈ 0.30–0.32, which is below 0.35 and would be rejected. The delta gate at 0.20 already catches lottery strikes; 0.35 was redundant and over-tight. 0.25 is the correct floor.

### Bug 3 — Tradier bid/ask never propagated to call_spread/put_spread (aiem_options_scheduler.py ~line 1661–1678)

**Old:** Tradier fetch only updated `call_mid` from the live bid/ask. `call_bid`, `call_ask`, and `call_spread` retained BS-synthetic values (bid = mid × 0.88, ask = mid × 1.12, spread = 0.24 always).  
**Fixed:** Tradier fetch now also updates `call_bid`, `call_ask`, `call_spread`, `put_bid`, `put_ask`, `put_spread` when live data is available.

With the synthetic spread always at 0.24, the `bid_ask_spread_pct > 0.20` gate fired on every single execution — even for liquid options with real spreads of 7–15%. The gate never had a chance to pass.

## Gate flow after fix

On a real trading day with Tradier data available:
- `delta`: corrected strikes at ~2.5% OTM → delta ≈ 0.27–0.36 → passes `< 0.20` gate ✓
- `probability_estimate`: Tradier delta used, ≈ 0.28–0.36 → passes `< 0.25` gate ✓
- `bid_ask_spread_pct`: live Tradier bid/ask ≈ 7–15% → passes `> 0.20` gate ✓
- `slippage_pct`: derived from spread × 0.5 ≈ 3–8% → passes `> 0.15` gate ✓
- `volume`, `open_interest`: OSS pre-screened candidates; Tradier typically returns >100 vol, >500 OI ✓
- `dte`: hardcoded to 9 → passes `< 5` gate ✓

Weekend/Tradier-failure fallback: volume=0, OI=0, spread=0.24 → gates correctly reject (no trade without live data).

## Side note — options_pipeline_jobs historical FAILED vs NO_TRADE_GATES

Pre-fix code wrote `status='FAILED'` for all exceptions. Post-fix code classifies gate-rejection errors as `NO_TRADE_GATES`. Historical rows (July 20–31) show `FAILED` from old code; new executions will correctly show `NO_TRADE_GATES`. No data correction needed.

## Live proof pending

First real execution window: Monday 2026-08-04 09:40 ET (seed) / 09:45 ET (execute). Expect first rows in `oe_decision_records` or `oe_no_trade_candidates` that Monday.
