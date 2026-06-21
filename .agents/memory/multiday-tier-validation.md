---
name: Multi-Day Runner tier threshold validation
description: Mid/small-cap thresholds are heuristic estimates — need to be validated against live outcome data in mid-July 2026
---

# Multi-Day Runner tier thresholds — validation needed

## The rule
Mid-cap (≥4%/STRONG≥7%) and small-cap (≥5%/STRONG≥10%) thresholds were set by logic, NOT backtested.
Only large-cap (≥3%/STRONG≥5%) has real backtest data: 59.7% WR / 69.6% WR.

## When to validate
~2–3 weeks after mid/small signals start firing (target: mid-July 2026).

## How to validate
Query `multiday_runner_outcomes` (or `multiday_runner_log`) grouped by `cap_tier`:
```sql
SELECT cap_tier,
       COUNT(*) AS signals,
       ROUND(AVG(CASE WHEN d5_pct > 0 THEN 1 ELSE 0 END)::numeric, 3) AS win_rate,
       ROUND(AVG(d5_pct)::numeric, 3) AS avg_d5_pct
FROM multiday_runner_log
WHERE d5_pct IS NOT NULL
GROUP BY cap_tier;
```
Compare mid/small win rates to large-cap baseline.
If mid WR < 55% → lower threshold to ≥3.5% or raise STRONG bar.
If small WR < 55% → lower to ≥4% or require STRONG-only display.

## Why
Smaller caps are more volatile so the bars were raised, but that's intuition not data.
The outcomes updater already stores D+5 returns so no extra instrumentation is needed.
