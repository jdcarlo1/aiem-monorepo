---
name: AEIM Diagram 2 Phase 4 (Discovery Engine) findings
description: Verification results for Phase 4 of the 18-phase AEIM master wiring/verification project — largest phase so far, first genuine DOCUMENTED_DORMANT module finding.
---

## Result
14/15 modules wired directly. 1/15 (`behavioral_fingerprint.py`) is a genuine, DOCUMENTED_DORMANT
module — real correct code, deliberately not wired in yet.
26/26 tools registered in the dispatch map.

## behavioral_fingerprint.py — first DOCUMENTED_DORMANT verdict
Module's own docstring states it was extracted from main.py's inline fingerprint math so
BOTH main.py and aiem_autonomous.py could share one source of truth, but explicitly says
main.py's own `_compute_fingerprint`/`_cosine_sim` (confirmed real, used at 6+ call sites) are
"left as-is for now ... this module is the new source of truth going forward; main.py can be
migrated to wrap it later without behavior change." This is DOCUMENTED_DORMANT, distinct from
VERIFICATION_FAILED (accidental gap) — proven by reading the module's own header, not assumed.
**Reusable check**: before flagging an unwired module as a gap, always read its own docstring/
header first — it may already document the gap as an intentional, deferred migration.

## Module-count vs tool-count is not 1:1
`causal_inference.py` is genuinely wired (via `granger_precedence_test`), but the tool that
calls it (`run_granger_test`) is NOT one of Phase 4's 26 registered tools — it lives in the
tool map under a name outside this phase's PHASE_TOOLS list. Module wiring proof and tool-phase
mapping are two separate checks; don't assume a wired module implies its caller tool is
phase-tagged to the same phase.

## Tool ownership split
13/26 genuinely module-file-owned (aiem_discovery_engine.py backs 6 tools via one
`get_discovery_engine()` singleton; hypothesis_registry.py backs 2; active_hypothesis_selection.py,
causal_discovery.py, historical_analog_search.py, breakout_signature_discovery.py back the rest).
13/26 inline in main.py (predefined hypothesis batteries, curated composite-indicator lists,
direct psycopg2 queries on pre_move_templates/behavioral_pattern_matches/aiem_signal_discoveries/
polygon_market_daily). All 13 inline functions added to aiem_function_registry_build.py as
PHASE4_FUNCTIONS.

## Safety notes confirmed by reading code (not assumed)
`discovery_run_cycle` writes ONLY to `discovered_candidates`; `discovery_promote_candidate`
explicitly does NOT wire a candidate into any live/paper execution path — integration into
SignalFactory requires a separate manual step per its own returned spec.
