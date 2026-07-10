---
name: Diagram 2 Phase 10 (Specialist Council / Debate) verification findings
description: Static wiring/tool-trace results for Phase 10 of the AEIM Diagram 2 master wiring project — cleanest phase yet, but with a third naming trap.
---

Scope: 2 modules (specialist_council.py, bull_bear_debate.py) + 2 AI tools
(adversarial_review, strategy_ensemble), per `aiem_registry.py` PHASE_TOOLS[10].
Verify script: `artifacts/stock-scanner-api/aiem_phase10_verify.py` (grep/sed
static checks only, never live-imports main.py).

**Modules: 2/2 VERIFIED_WIRED, 0 gaps.** Both are direct `import` hits in
main.py's "AIEM specialist modules" bootstrap (the same try/except block as
fred_macro/social_sentiment/drift_alarm/etc.), each with genuine downstream
usage traced by sed:
- `specialist_council.py` — `SpecialistOpinion`/`compute_weighted_verdict`
  called at two independent sites inside `_aiem_paper_pick_candidates()`
  (weighted multi-signal negotiation, applied as a score multiplier).
- `bull_bear_debate.py` — `run_bull_bear_debate()` called once, for the top-3
  picks in the same paper-trading pipeline (GPT vs Claude adversarial debate).

Neither module is reached via any AI-callable tool — both are wired straight
into the paper-trading pick-scoring pipeline, invisible to the tool-dispatch
layer entirely. This is architecturally fine (same carrier function pattern
as Washout Ignition), just worth knowing when reasoning about "is X reachable
from an AI tool call."

**Tools: 2/2 registered, 0 gaps — but 0% are Phase-10-module-owned (a first).**
- `adversarial_review` → real implementation is `adversarial_critique.py`
  (Phase 4, Discovery Engine). **Third naming trap in the project** (after
  Phase 5's `mkt_compute_indicators`≠indicators.py and Phase 9's
  `analyze_signal_correlation`): the tool name strongly implies
  specialist_council/bull_bear_debate but has zero code relationship to
  either.
- `strategy_ensemble` → real implementation is `aiem_level3.py` (Phase 1),
  the exact same implementation already verified in Phase 9. This tool is
  legitimately double-tagged across `PHASE_TOOLS[9]` and `PHASE_TOOLS[10]` in
  `aiem_registry.py` — one real implementation, not double-counted, DB
  `verification_version` for this tool row is now 2 (touched by both phase
  scripts).

No `PHASE10_FUNCTIONS` list was added to `aiem_function_registry_build.py`:
that registry only tracks *inline* main.py tool functions with no dedicated
module file (see PHASE9_FUNCTIONS pattern). Both Phase 10 tools have real
module files (owned by other phases), so they don't fit that registry and
are fully covered by the `aiem_tool_registry` update instead.

## Stage 15 audit-trail mislabel (found 2026-07-10, unfixed)
The Diagram-2 pipeline trace audit's "stage 15 (specialist_council)" label is
wrong: that stage actually measures the `bull_bear_debate.py` reuse path, not
`specialist_council.py`. The real specialist_council weighted-verdict module
runs unconditionally on all candidates and never fails, so any failure the
audit trail attributes to "stage 15" is really a bull/bear debate failure.
**Why it matters:** don't trust "stage 15 failed" to mean specialist_council
is broken — check which module actually ran before debugging. Fix belongs in
the trace-audit stage-naming, not in either module itself.
