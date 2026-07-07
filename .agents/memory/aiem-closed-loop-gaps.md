---
name: AIEM closed-loop learning implementation
description: All 5 audit gaps from AIEM_ADAPTIVE_LEARNING_PROOF_REPORT are now closed; architecture and key files for each gap.
---

## Five gaps and their fixes

**Gap 1 — audit_trace_id + learning_update_applied step**
- `log_learning_update_step()` in `aiem_closed_loop_learning.py`
- Called from `_aiem_paper_mark_to_market()` after every trust update
- Writes to `aiem_pipeline_audit_log` with `module_name='learning_update_applied'`

**Gap 2 — signal_trust_history: before/after per update**
- `record_trust_update()` called before signal_trust_weights upsert in MTM
- signal_trust_history now has 18 columns (old/new trust, delta, trade_id, ticker, pnl, audit_trace_id)

**Gap 3 — Thompson sampler**
- `aiem_paper_thompson` table (1 row per signal source, seeded from historical trades)
- `aiem_paper_thompson_history` table for before/after audit
- `update_paper_thompson()` called in MTM after every exit
- Increments α (win) or β (loss); draws Beta(α,β) sample as sampled_score

**Gap 4 — PPO training runs**
- `maybe_run_ppo_training()` called from RL pipeline bg thread after every MTM
- Logs to `rl_training_runs` — gradient_step_completed=TRUE only when buffer≥10
- Current threshold: 10 real experience buffer rows (not dummy/seeded)

**Gap 5 — Candidate rankings**
- `_add()` now stores raw_score, drift_mult, trust_mult per candidate
- `store_candidate_rankings()` called at end of `_aiem_paper_pick_candidates()`
- Stores ACCEPTED + REJECTED candidates with all multipliers to `aiem_candidate_rankings`

## Key files
- `aiem_closed_loop_learning.py` — all 5 helper functions
- `aiem_closed_loop_migration.sql` — idempotent DDL for all new tables
- `main.py` — 7 surgical edits (pick candidates + MTM + startup init)

## Admin endpoints
- `GET /stock-api/admin/closed-loop-audit/<trade_id>` — per-trade PASS/PARTIAL/FAIL
- `GET /stock-api/admin/closed-loop-summary` — fleet summary

**Why:** Every alert must be traceable from signal source → pick decision → trade outcome → learning update, with DB evidence at each step. This closes the proof requirement for AIEM as a real adaptive system, not just a scanner with a paper trading tab.
