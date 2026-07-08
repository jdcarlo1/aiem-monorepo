---
name: Diagram 2 Phase 6 findings — Options & Smart Money Flow
description: Wiring/verification results for the 8 Phase 6 modules and 11 Phase 6 AI tools; first genuine tool-registration gap found in the Diagram 2 project.
---

## Modules (8) — 7/8 wired, 1/8 verified not-wired-by-design, 0 genuine gaps
- `aiem_options_structure.py`, `congress_trades.py`, `insider_trades.py`,
  `microstructure_proxy.py`, `options_sweep.py`, `smart_money.py`,
  `smart_money_divergence_detector.py` — all directly imported into main.py
  (module-level or lazy), confirmed by grep.
- `fetch_si_background.py` — VERIFIED_NOT_WIRED_BY_DESIGN. Own docstring says
  it's a standalone daemon run manually
  (`python3 fetch_si_background.py >> /tmp/fetch_si.log 2>&1 &`). Zero
  references anywhere in the repo. Same category as Phase 2's
  lookahead_audit.py/manual_rollback.py.

## Tools (11) — 10/11 registered, 1/11 GENUINE GAP
- 1 module-owned (Phase 6): `microstructure_proxy` → microstructure_proxy.py
- 2 cross-phase-owned: `option_b_evaluate`, `option_b_status` →
  aiem_intelligence_layer.py (Phase 1). "Option B" here is a generic
  decision-brain (TRADE/REDUCE_SIZE/WAIT/NO_TRADE), **unrelated** to the
  "Module B Short Squeeze" signal from earlier sessions — pure naming
  coincidence, confirmed by reading the handler body.
- 7 inline (main.py direct SQL, reading tables populated by Phase 6 modules):
  `mkt_cross_confirm_options`, `mkt_gex_scan`, `mkt_net_flow_db`,
  `mkt_options_flow_scan`, `mkt_options_predicts_price`, `mkt_options_skew`,
  `mkt_ticker_options_history`.
- **GAP: `smart_money_divergence`** — this string is NOT a dispatch-map tool
  key anywhere in main.py. It only exists as the default value of the
  `signal_name` parameter inside `_aiem_tool_divergence_scan` (registered as
  tool `divergence_scan`, already credited to Phase 5) and in an unrelated
  outcome-recording tool's description text. The Phase 6 tool registry entry
  does not correspond to any real callable AI tool. Reported honestly as
  VERIFICATION_FAILED — this is the project's **first genuine
  tool-registration gap** found across Phases 0-6 (as opposed to the
  by-design daemon-script pattern for modules).

## Artifacts
- `aiem_phase6_verify.py` (follows the Phase 5 verify-script template)
- `PHASE6_FUNCTIONS` (7 entries) added to `aiem_function_registry_build.py`
