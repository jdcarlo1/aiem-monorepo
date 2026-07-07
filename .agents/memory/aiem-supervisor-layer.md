---
name: AIEM Supervisor Meta-Reasoning Layer
description: 7-module autonomous supervisor above AIEM — risk manager, teacher, auditor; fires post-trade/daily/weekly
---

## Architecture

`aiem_supervisor.py` + `aiem_supervisor_migration.sql` in `artifacts/stock-scanner-api/`.

AIEM is decision_authority. Supervisor is meta_authority — never silently overrides, every action logged to DB.

## 7 Modules

| Module | Function | Table |
|--------|----------|-------|
| M1 Loop Audit | Verifies 6-step closed-loop chain per trade | `aiem_supervisor_loop_audit` |
| M2 Bad Learning | Detects excessive weight changes, lucky wins, consecutive losses | `aiem_supervisor_bad_learning_flags` |
| M3 Risk Control | Pre-pick gate: max trades/day, same-source concentration, ticker already open, frozen lifecycle | `aiem_supervisor_risk_checks` |
| M4 Performance Grader | A-F grade from WR + learning_quality + risk_discipline + calibration | `aiem_supervisor_performance_reports` |
| M5 Signal Lifecycle | WATCHLIST/ACTIVE/PROMOTED/DEMOTED/FROZEN/RETIRED per signal source | `aiem_supervisor_signal_lifecycle` |
| M6 Overfit Protection | IS vs recent WR degradation + OOS edge + outlier dependency | `aiem_supervisor_overfit_checks` |
| M7 Override Log | Immutable audit of every supervisor action that overrides AIEM | `aiem_supervisor_overrides` |

Also: `aiem_supervisor_signal_health` view (joins `aiem_paper_trades` + lifecycle).

## Wiring in main.py (5 edits)

1. `_aiem_paper_pick_candidates` → `run_post_pick_supervisor` per candidate (non-blocking try/except)
2. `_rl_pipeline_bg` after PPO block → `run_post_trade_supervisor` batch (queries today's closed trades from DB)
3. 5 admin routes: `/admin/supervisor-{summary,daily-report,weekly-report,signal-lifecycle,overfit-check}`
4. `_DEFERRED_INITS` → `init_schema()` on server boot
5. Scheduler: Mon-Fri 4:50 PM ET daily report, Sunday 6 PM ET weekly report

## Thresholds

- `_MIN_SAMPLE = 10` trades before trust changes count
- `_MAX_WEIGHT_CHANGE = 0.08` per single trade
- `_MIN_WR_PROMOTION = 0.55`, `_MAX_WR_RETIREMENT = 0.35`
- `_FREEZE_CONSECUTIVE_LOSSES = 5`
- `_OVERFIT_SAMPLE_MIN = 30`

## Live First-Run Verdict (2026-07-07)

From 101 closed paper trades:
- `multi_signal` → **RETIRED** (0.0% WR / 39 trades) — override logged
- `gap_volume` → **DEMOTED** (28.9% WR / 45 trades) — override logged
- `unusual_calls` → **DEMOTED** (0.0% WR / 13 trades)
- Weekly grade: **D** (WR=10.4%, 48 trades past 7 days)

**Why:** M5 runs on demand (via admin endpoint) and on every MTM via M1-M4. Loop audit / bad-learning / risk rows populate only when trades close (MTM runs 4:01 PM ET on trading days).
