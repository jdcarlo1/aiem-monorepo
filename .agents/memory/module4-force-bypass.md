---
name: Module 4 force-bypass
description: apply_action() force=True pattern for statistical invalidity (not decay) — when and why to use it
---

`apply_action()` in `aiem_module4_gate.py` now accepts `force: bool = False`.

**Rule:** Only use `force=True` when the reason for retirement is *proven mathematical invalidity* of the stored statistics — NOT for observed performance decay, which requires the normal Module 2 time-accumulation gate.

**Why:** The `evaluable_now` time-gate exists because decay detection requires N live observations to accumulate. But when stored n/WR/p are hand-entered or externally sourced, the Module 2 time-gate is the wrong gate — the signal is invalid from day 0 regardless of observations. The three-proof pattern (p-value inconsistency, exact-50% baseline, n unreproducible from DB) is the bar for using force.

**How to apply:**
- Pass `"force": true` in the JSON body to `POST /stock-api/admin/module4-approve`
- The endpoint passes `force=bool(body.get("force", False))` to `apply_action()`
- When `force=True` and `eval_status != "evaluable_now"`, the audit reason is prepended with `[FORCE-OVERRIDE: eval_status=...]` automatically
- The full documented justification goes in the `reason` field — it lives in `aiem_signal_actions` forever

**Three-proof template used for id=4:**
1. Stored p-value is N orders of magnitude from recomputed Fisher p for the same n/WR
2. baseline_win_rate=50.0% exactly (never a real query result)
3. n cannot be reproduced from polygon_market_daily under any plausible condition reconstruction
