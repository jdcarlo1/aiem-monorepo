---
name: Diagram 2 Phase 12 (Edge Filter & Exit Engine) verification findings
description: Static wiring/tool-trace results for Phase 12 — lowest same-phase tool ownership yet, a second cron-only module, a 4th naming trap, and a genuinely out-of-catalog module.
---

Scope: 2 modules (aiem_edge_filter.py, aiem_exit_engine.py) + 9 AI tools, per
`aiem_registry.py` PHASE_TOOLS[12]. Verify script:
`artifacts/stock-scanner-api/aiem_phase12_verify.py`.

**Modules: 2/2 VERIFIED_WIRED, 0 gaps.**
- `aiem_edge_filter.py`: genuinely AI-tool-reachable (edge_filter_status,
  edge_filter_evaluate) PLUS wired into the internal paper-trading gate
  (hard-blocks on confirmed 'negative' edge) PLUS MTM closed-trade
  processing PLUS deferred startup schema init.
- `aiem_exit_engine.py`: wired ONLY via a 30-minute scheduler cron job
  during market hours (`aiem_exit_review`, 9-15 ET) calling
  `review_open_positions()`. **No AI tool reaches it at all.** Second
  confirmed instance (after Phase 10's specialist_council.py/
  bull_bear_debate.py) of "genuinely wired into live execution but
  invisible to any AI tool call" — this time via cron instead of the
  paper-trading pipeline. Worth tracking as its own wiring category
  distinct from "AI-tool-reachable" and "paper-pipeline-only".

**Tools: 9/9 have a real dispatch-map entry (0 dispatch gaps) — but only
2/9 are genuinely Phase-12-owned, the lowest same-phase ratio since Phase
9/10** (contrast Phase 11's 9/10). Breakdown of the other 7:
- `run_risk_gate` → pre_decision_risk_gate.py (Phase 11)
- `rl_get_paper_action` → rl_position_sizer.py (Phase 11)
- `deep_rl_get_paper_action` → deep_rl_policy.py (Phase 15)
- `get_decisions` / `log_decision` → decision_logger.py (Phase 9)
- `query_exit_timing` → **inline direct SQL** on `ai_short_calls_log`, zero
  relationship to aiem_exit_engine.py despite the name — 4th naming trap
  found in this sweep (after Phase 6's smart_money_divergence, Phase 9's
  analyze_signal_correlation, Phase 10's adversarial_review).
- `holding_period_optimize` → `holding_period_optimizer.py`, a **real
  module that is genuinely imported and called**, but is **absent from the
  195-module aiem_module_registry catalog entirely** (verified: 0 rows
  match `ILIKE '%holding_period%'`, total registry count unaffected at
  195). First "real+called+out-of-catalog" module found in this sweep.
  Deliberately documented, NOT silently added as a new registry row —
  expanding the tracked 195-module catalog is out of scope for a
  verification pass and should be a deliberate decision, not a side effect.

Lesson: when a tool's real implementation module isn't in
`MODULE_PHASE_MAP`/the DB registry at all, don't skip past it or force-fit
it into a phase — flag it explicitly as out-of-catalog so a future
decision-maker can choose whether to formally add it.
