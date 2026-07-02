---
name: AIEM Gate-Integrity Audit Modules
description: Architecture and verified state of Modules 2/3/4/5 that form the signal quality gate pipeline
---

# AIEM Gate-Integrity Audit Modules

## Sunday Pipeline (ET, sequential)
- **Module 2** `aiem_module2_decay.py` — 2:30 AM — evaluates validated signals for decay/retirement
- **Module 3** `aiem_module3_promotion.py` — 3:00 AM — evaluates hypothesis signals for promotion
- **Module 5** `aiem_module5_discovery.py` — 4:00 AM — discovers new signal candidates via Fisher+BH-FDR

Module 4 is not scheduled — it is the human approval gate triggered manually via endpoints.

## Module 4 (Human Approval Gate)
- Schema: `aiem_signal_actions` table
- Endpoints: `GET /admin/module4-pending`, `POST /admin/module4-approve`, `GET /admin/module4-history`
- Valid actions: `retire` → retired, `downgrade` → hypothesis, `keep` → no change, `promote` → validated
- `apply_action()` requires non-empty `reason`; logs to `aiem_signal_actions`
- Pending signals: `evaluable_now` status from M2 with `decay_verdict IN ('failing','decaying')` and no recent approval

## Module 3 (Hypothesis Promotion Evaluator)
- Schema: `aiem_module3_evaluations` UNIQUE(discovery_id)
- Promotion criteria (all required): n>=30, wr>=52%, p<0.10, delta>=-7.5pp vs discovery wr
- Retirement recommendation: n>=30, wr<50%, p<0.05
- 5 statuses: `promote_ready`, `hypothesis_failing`, `borderline`, `accumulating`, `no_outcome_yet`, `structural`
- Structural signals (key set intersects `{cross, funnel, trough, confirm, fire_day, universe}`) → skip
- Endpoints: `POST /admin/run-module3-promotion` (admin), `GET /aiem/module3-status` (public)
- Current state (2026-07-02): id=2,3 → no_outcome_yet; id=4 → accumulating n=9 (need 21 more); id=9 → structural

## Module 5 (Pattern Discovery Engine)
- Grid: 25 conditions × 3 horizons (1d/3d/5d) = 75 tests on polygon_market_daily (3.3M rows, ~80s)
- Test: Fisher's exact test (one-tailed), BH-FDR correction at alpha=0.05
- Qualification: cond_n>=50, cond_wr>=55%, delta_wr>=3pp, BH-FDR rejected, dedup by conditions_json key set
- Inserts qualifying discoveries as `status='hypothesis'` with `invented_indicator='module5_fisher_bh'`
- Schema: `aiem_module5_runs` + `aiem_module5_test_results`
- Endpoints: `POST /admin/run-module5-discovery` (admin, ~80s), `GET /aiem/module5-status` (public)
- Key finding: simple momentum (rvol, change_pct, gap_pct) shows NO forward edge; `close_strength_gte_0_90` h=5d IS significant

## aiem_signal_discoveries Column Names
**Critical**: always use these exact column names when inserting:
- `signal_win_rate`, `signal_n` — condition group stats
- `baseline_win_rate`, `baseline_n` — control group stats
- `edge_broad` — delta (condition wr - baseline wr)
- `oos_edge` — OOS delta (same as edge_broad at discovery time)
- `p_value` — raw p-value from test
- `invented_indicator` — method tag (e.g., 'module5_fisher_bh')
- `discovered_at`, `confirmed_at` — timestamps
- **Does NOT have**: oos_win_rate, oos_n, backtest_method, status_history, module_source

## Why
The gate-integrity audit exists because id=1 was found to have oos_edge=NULL despite being 'validated' status — proving the signal was never properly OOS-tested before promotion. Modules 2-5 together enforce: all validated signals have positive OOS evidence (M2), all hypothesis signals accumulate before promotion (M3), human reviews every status change (M4), and new hypotheses require statistical validation across 3.3M-row grid (M5).
