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

## Module 6 (Rediscovery Engine)
- Trigger: immediately on `action=retire` in module4-approve (background thread) + Sunday 4:15 AM
- Tests ≤6 pre-registered variations per retired signal as one combined BH-FDR batch
- Qualification: δ≥2.5pp, WR≥52%, n≥50, BH-FDR rejected (α=0.05)
- Generation cap: only gen=0 signals can spawn children (gen=1 → lineage closed)
- Schema: adds `parent_signal_id/generation/variation_note` to aiem_signal_discoveries + `aiem_rediscovery_runs`
- First live result: id=1 (quiet accumulation) → id=11 descendant (horizon: next_day→3d, WR=56.17%, δ=3.92pp, n=29752)
- Variation generator: PRIMARY key only varied (first testable key in conditions_json); non-testable keys (prior_day_*, CMF, RSI, funnel) → logged as non_testable, no variations
- `_T2_MIN_DAYS` alias defined after `_classify_tier` — fine in Python (module-level lookup at call time)

## Module 7 (Sector Rotation Detector)
- Universe: 12 sector ETFs (XLK/SMH/XLF/XLRE/XLE/XLV/XLI/XLY/XLP/XLU/XLB/XLC) vs SPY
- Data: polygon_market_daily — all 13 tickers present with 498 days; NO API calls; 0.8s runtime
- rvol column = volume ratio (already computed); change_pct computed inline
- Tier 1 (log only): |1d RS| ≥ 1.5×60d SD OR rank jump ≥4 positions
- Tier 2 (Telegram watch): RS>0 on 3d+5d, rvol>1.2 on ≥2 of last 3 days
- Tier 3 (Telegram confirmed + Layer 10 input): RS>0 on 3d+5d+20d, top/bottom-3 rank for ≥4 consecutive days
- Tier 3 requires consecutive rank HISTORY — can only activate after ≥4 daily runs have been stored
- Layer 10 integration: `get_sector_state(conn, etf_ticker)` / `get_all_tier3_sectors(conn)`
- Schema: `aiem_sector_rotation` (UNIQUE date+sector_ticker) + `aiem_sector_alerts_log`
- Scheduler: Mon-Fri 5:00 PM ET daily (not Sunday-only)

## Full pipeline schedule
- Sunday: M2@2:30AM → M3@3:00AM → M5@4:00AM → M6@4:15AM
- Weekdays: M7@5:00PM ET

## Why
The gate-integrity audit exists because id=1 was found to have oos_edge=NULL despite being 'validated' status — proving the signal was never properly OOS-tested before promotion. Modules 2-7 together enforce: all validated signals have positive OOS evidence (M2), all hypothesis signals accumulate before promotion (M3), human reviews every status change (M4), new hypotheses require statistical validation (M5), retired signals get one clean retry on neighboring conditions (M6), and sector context weights conviction scores (M7).
