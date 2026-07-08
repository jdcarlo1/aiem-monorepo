---
name: AEIM Diagram 2 Phase 3 (Macro & Regime Context) findings
description: Verification results for Phase 3 of the 18-phase AEIM master wiring/verification project — cleanest phase so far, 2 more transitive-wiring examples, cross-phase tool pattern repeats.
---

## Result
0 genuine module gaps, 0 by-design orphans — cleanest phase yet (12/12 modules wired).
13/13 tools registered in the dispatch map.

## Two more transitive-wiring examples (same pattern as Phase 2's order_dedup.py)
- `regime.py` (bare `detect_regime(df)` function) has zero direct references in main.py.
  Its only real caller anywhere in the repo is `prop_signal.py`
  (`from regime import detect_regime`), and `prop_signal.py` IS imported directly by
  main.py (`from prop_signal import prop_signal`).
- `regime_macro_patch.py` has zero direct references in main.py. Its only real caller is
  `premarket_open_trader.py` (`import regime_macro_patch as rmp` +
  `rmp.get_regime_with_macro_overlay(...)`), and `premarket_open_trader.py` is already a
  known carrier (it also transitively wires order_dedup.py in Phase 2).
**Reusable check**: before calling a module with zero direct main.py hits an orphan,
repo-grep for its exported names/functions to find real callers, then check if THAT
caller is wired into main.py.

## Cross-phase tool ownership keeps compounding
`aiem_risk_guards.py` (Phase 11) is now the real implementation behind 6 total
"guardrail-flavored" tools spread across Phase 2 and Phase 3's tool sets (4 in Phase 2:
correlation/liquidity/circuit-breaker status+reset; 2 in Phase 3: event_risk_check,
event_risk_filter_status). `aiem_pullback_reentry.py` (Phase 5) backs 2 tools across
Phase 2 (check_signal_data_availability) and Phase 3 (run_regime_filtered_backtest).
**Lesson reinforced**: a tool's phase tag names its category/intent, not its real code
owner — always trace the handler body before crediting a phase.

## 4 inline Phase 3 functions (no module file)
query_market_regime, momentum_macro_regime, mkt_regime_filter, mkt_term_structure — all
direct psycopg2 queries in main.py (spy_daily_cache joins, sector_etf_daily breadth,
polygon_market_daily SPY-gap regime split, options_structure_scan term-ratio lookup).
Registered as PHASE3_FUNCTIONS rows in aiem_function_registry_build.py. Note:
mkt_term_structure does NOT call aiem_options_structure.py (a separate real Phase 6
module) despite the topical overlap — confirmed by reading the body, not assumed.
