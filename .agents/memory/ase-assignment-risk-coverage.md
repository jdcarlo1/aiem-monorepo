---
name: ASE assignment & risk coverage logic bugs
description: Two bugs found in assignment.py during S9 verification; fixes and root causes documented.
---

## Bug 1 — Bear call spread coverage (T09)

**Rule:** `_is_call_covered` originally required `lc_k <= sc_k` (long call at lower strike).
**Problem:** A bear call spread has SC at the *lower* strike and LC at the *higher* strike — e.g., SC@110 + LC@115. The LC@115 IS the protective wing but `lc_k (115) <= sc_k (110)` is False → incorrectly flagged as naked.

**Fix:** In `partial_assignment_impact`, replaced per-leg strike comparison with net ratio count:
```
net_sc = Σ ratio(short calls) − Σ ratio(long calls) − len(long stock)
net_sp = Σ ratio(short puts)  − Σ ratio(long puts)  − len(short stock)
has_naked = (net_sc > 0 or net_sp > 0)
```
This correctly handles both bull-call (LC lower, SC higher) and bear-call (SC lower, LC higher) spreads.

**Why:** Coverage is about net exposure, not strike ordering. Any long call in the structure (regardless of relative strike) limits the loss — what matters is whether the total long count matches or exceeds the total short count.

**How to apply:** Whenever checking "is a short option covered?" in a multi-leg structure, count net ratios, not strike comparisons.

---

## Bug 2 — ATM straddle likelihood threshold (T10)

**Rule:** `multi_leg_assignment_analysis` used `abs_delta >= 0.60` for MEDIUM assignment likelihood.
**Problem:** ATM short straddle has delta ≈ ±0.50 — below 0.60 → classified as NONE despite DTE=2.

**Fix:** Lowered threshold to `abs_delta >= 0.40 and dte <= 5` so ATM short options at short DTE register MEDIUM.

**Why:** At-the-money options near expiry have meaningful assignment risk (pin risk, gamma maximised). The 0.60 threshold was designed for ITM detection and too strict for ATM near-expiry.

**How to apply:** Any assignment risk model for short options should treat delta ≥ 0.40 at DTE ≤ 5 as MEDIUM, not NONE.
