---
name: AEIM Diagram 2 Phase 5 (Technical Signal Layer) findings
description: Verification results for Phase 5 of the 18-phase AEIM master wiring/verification project — highest cross-phase tool-ownership ratio seen so far, and a naming-collision trap between two unrelated code paths.
---

## Result
12/12 modules wired (0 genuine gaps). 26/26 tools registered in the dispatch map.

## Two genuine transitive-wiring cases (not fabricated — full chain verified)
- `advanced_quant_indicators.py` has exactly one caller in the whole repo:
  `layer9_statistical_edge.py` (`from advanced_quant_indicators import (...)`). That file is
  itself directly imported into main.py at 6 call sites
  (`from layer9_statistical_edge import compute_layer9_score/batch_layer9_scores/
  format_layer9_signal`). Both links of the chain were checked independently before
  crediting the leaf module as wired.
- `indicators.py` is never imported by main.py directly, but 4 sibling modules
  (`scanner.py`, `backtest.py`, `composite_scan.py`, `aiem_level2.py`) import
  `compute_indicators`/`build_history` from it, and all 4 of those siblings ARE directly
  imported into main.py. Same two-link verification pattern.

**Reusable check**: for any "no direct import found" module, always grep the whole repo for
its real callers before marking VERIFICATION_FAILED — then verify the caller itself is wired
into main.py. A module can be genuinely live without ever being named in main.py.

## Naming-collision trap: mkt_compute_indicators vs indicators.py
The AI tool `mkt_compute_indicators` sounds like it should call `indicators.py`, but reading
the function body shows it's a fully independent, hand-rolled reimplementation (manual
`_sma`/`_ema`/RSI/MACD/ADX/Bollinger/Keltner/OBV/MFI/CMF helpers, numpy-only, no import of
the `indicators` module at all). Meanwhile `indicators.py`'s real callers are the 4 sibling
scripts above. Two unrelated, both-real code paths that happen to share vocabulary — don't
assume a tool implements the module with the matching name; always read the function body.

## Highest cross-phase tool ratio so far: 5/26
`check_price_bullish` + `divergence_scan` → `smart_money_divergence_detector.py` (Phase 6);
`gap_continuation_score` + `intraday_compute_features` + `intraday_continuation_score` →
`premarket_gap_continuation_scanner.py` / `intraday_continuation_scanner.py` (both Phase 0).
All confirmed as real modules genuinely wired, just tagged to a different phase's registry
bucket than the tool itself. Reinforces the Phase-0 lesson: "Phase N tool" means
AI-capability-tagged-to-N, not implementation-must-live-in-an-N-file.

## Tool ownership breakdown
16/26 genuinely module-file-owned (11 of those to a Phase 5 file, 5 cross-phase).
10/26 inline in main.py — direct psycopg2/numpy scans over `polygon_market_daily`
(mkt_52week_momentum, mkt_accumulation_squeeze, mkt_capitulation_detector,
mkt_compute_indicators, mkt_compute_momentum, mkt_extreme_move_reversion,
mkt_pre_squeeze_warning, mkt_price_patterns, mkt_quiet_accumulation, mkt_volume_patterns).
All 10 added to `aiem_function_registry_build.py` as `PHASE5_FUNCTIONS`.
