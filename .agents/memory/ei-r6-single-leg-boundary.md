---
name: EI R6 single-leg boundary
description: R6 (fill_prob < 0.30) is mathematically unreachable for single-leg strategies that pass R2-R5; only reachable via multi-leg aggregation
---

## Rule

For a **single-leg** strategy, the `compute_leg_fill_probability` formula is
bounded such that any leg satisfying R2 (spread_pct < 0.35), R3 (OI ≥ 50),
R4 (volume ≥ 20) necessarily has `fill_prob ≥ 0.30`.

The worst-case single-leg combination:
- spread_pct = 0.30 → spread_adj = -0.15
- volume = 20 → vol_adj = 0.0
- OI = 55 → oi_adj = 0.0
- depth (ask_size for BUY) = 0 → size_adj = -0.05
- fill_prob = 0.50 - 0.15 + 0.0 + 0.0 - 0.05 = **0.30 exactly**

The R6 check is `fill_probability < EI_MIN_FILL_PROB` (strict `<`), so 0.30 does
NOT trigger R6.

## How R6 IS reachable

Multi-leg aggregation:
```python
agg_fill_prob = (product_of_per_leg_fps + min_per_leg_fp) / 2
```
For 2 legs each at fp=0.30: (0.30 × 0.30 + 0.30) / 2 = (0.09 + 0.30) / 2 = **0.195 < 0.30** → R6 fires.

## Why

This is intentional co-design: the quality gates (R2/R3/R4) and the fill_prob
formula share the same threshold values, so passing the gates guarantees sufficient
per-leg fill probability. R6 serves as a **multi-leg penalty gate** only.

## How to apply

Any NC5-style negative control for R6 must use a **2-leg (or more) strategy**
where each leg individually passes R2–R5 but the aggregated fill_prob drops
below 0.30. Single-leg R6 isolation is not achievable with current parameters.

Also confirmed: `MIN_VOLUME = 20` (not 5) in `aiem_strat_engine/config.py`.
Using volume < 20 triggers R4 before R6 is ever evaluated.
