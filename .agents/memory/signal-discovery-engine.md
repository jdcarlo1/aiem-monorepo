---
name: Signal Discovery Engine
description: Three AIEM tools for autonomous hypothesis testing + daily continuous research loop
---

## What it is
Three tool functions added to the Sunday AIEM research agent and a daily autonomous research loop:
- `list_signal_dimensions()` — shows all queryable fields with distributions; call first
- `test_new_signal(conditions, target, lookback_days, segment_by, compare_to)` — tests any hypothesis vs real signal_outcomes data; returns n/WR/p-value/CI/verdict
- `analyze_missed_movers(min_move_pct, lookback_days)` — finds stocks that moved big but weren't caught; auto-generates hypothesis candidates

## Daily loop: `_run_aiem_continuous_research()`
- Scheduled 6 PM ET Mon-Fri (id="aiem_continuous_research")
- Tests 11 standard hypothesis templates; saves p<0.05 findings to aiem_research_insights
- Admin trigger: POST /stock-api/admin/run-aiem-continuous-research

## Condition syntax (safe allowlist parser)
Numeric: `call_put_ratio > 2.0`, `premium_m > 0.5`, `sweep_vol_oi > 5`, `sweep_premium_m > 1.0`, `sweep_iv > 0.8`, `days_out < 21`, `otm_pct > -10`
Boolean: `has_sweep = true`
Categorical: `session = market-open`, `day_of_week = Tuesday`, `cap_tier = nano`

## Data available for hypothesis testing
- `signal_outcomes`: 532 graded rows (Jun 5-22 2026), baseline T3 WR=49.4%
- `unusual_calls_log`: 293 overlap with signal_outcomes (sweep data)
- Any finding with p<0.05, n>=15 → "STATISTICALLY REAL — register this finding"

**Why:** The system needs to invent its own signals as data accumulates — not just analyze past picks. 
**How to apply:** Sunday system prompt now has a required signal discovery workflow section (Step A-E). The agent must test at least 3 novel hypotheses per session and register any p<0.05 findings.
