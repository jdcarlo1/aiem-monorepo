---
name: Adaptive Hypothesis Generation Layer
description: 8-module discovery cycle built on aiem_discovery_engine.py; all modules wired in main.py and verified live
---

## Module map (all in `_discovery_cycle_job` in main.py)

| Module | Function | What it does | Status |
|--------|----------|-------------|--------|
| M6 | `_discovery_cycle_job` itself | APScheduler CronTrigger Mon-Fri 17:30 ET; kill switch; DB concurrency guard | ✅ |
| M8 | `_dc_module8_notify()` | Telegram on error/proposed/daily-summary | ✅ |
| M1 | `_dc_module1_gp_weekly_job()` | GP signal evolution, Mon 17:35 ET, saves to `gp_discovered_templates` | ✅ |
| M2 | `_dc_module2_rank_templates()` | Thompson sampling via `active_hypothesis_selection.thompson_sample_category_value()` | ✅ |
| M3 | `_dc_module3_sgd_update()` | `online_learning.propose_update()` after each cycle | ✅ |
| M4 | `_dc_module4_adversarial_critique()` | `adversarial_critique.adversarial_review()` on pending candidates; auto-rejects `likely_overfit` | ✅ |
| M5 | `_dc_module5_promotion_check()` | `aiem_module3_promotion.run_module3()` on all hypothesis signals → `aiem_module3_evaluations` | ✅ |
| M7 | `_dc_module7_feedback_loop()` | M5 verdicts → `dc_template_feedback` by feature category; closes Thompson feedback loop | ✅ |

## Call order in `_discovery_cycle_job`
1. Kill switch check
2. DB concurrency guard (claim lock)
3. Module 2: rank templates (Thompson)
4. `aiem_discovery_engine.run_cycle(templates=_dc_ranked)`
5. Module 3: SGD weight update
6. Module 4: adversarial critique (only if `proposed > 0`)
7. Module 5: promotion/retirement evaluation
8. Module 7: feedback loop (M5 verdicts → dc_template_feedback)
9. Release lock + log update
10. Module 8: Telegram notification

## Key DB tables
- `discovery_cycle_log` — run history
- `discovery_cycle_config` — kill switch + running flags
- `discovered_candidates` — M4 updates `status='rejected'` for `likely_overfit`
- `aiem_module3_evaluations` — M5 upserts promotion classifications
- `dc_template_feedback` — M7 writes category-level success/failure verdicts
- `gp_discovered_templates` — M1 saves GP-evolved formulas

## Admin endpoints
- `POST /stock-api/admin/discovery-cycle/trigger` — manual trigger
- `GET  /stock-api/admin/discovery-cycle/status` — kill switch + last runs
- `GET  /stock-api/admin/discovery-cycle/report` — consolidated: runs + M5 state + M7 feedback
- `POST /stock-api/admin/discovery-cycle/enable|disable` — kill switch

## Verified log evidence
- M2: `Thompson top-8: ['T09', 'T01', 'T08', ...]` (order changes each run — working)
- M3: `accepted=True score=-0.197743 drift=0.0012`
- M5: `8 signals — no_outcome_yet=7 | structural=1`
- M7: `0 new promotion verdicts to record` (correct — no promote_ready/failing yet)
- Report endpoint: returns `last_runs`, `module5_state`, `module7_feedback`, `next_scheduled_run`

**Why:** M4 only runs when `proposed > 0` — the guard is intentional. M7's `dc_template_feedback` only accumulates when signals actually reach promote_ready or hypothesis_failing (needs weeks of OOS data).
