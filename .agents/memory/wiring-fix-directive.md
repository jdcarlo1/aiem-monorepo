---
name: Wiring Fix Directive Groups A/B/C (2026-07-11)
description: Implemented vs. blocked items from the 3-group wiring audit; key structural finding about aiem_signal_discoveries schema
---

## Implemented (2026-07-11)

- **A1**: `gamma_exposure_by_strike` deleted from `advanced_quant_indicators.py` — redundant with `_compute_gex` in `aiem_options_structure.py`
- **A4**: `aiem_probability_engine/walk_forward.py` tombstoned — zero callers confirmed, file now contains only a re-activation guide
- **A6**: `_drift_alarm.check_all_active_signals()` now called inside `_aiem_paper_drift_check()` (already scheduled 4:35 PM at main.py:15800); Fisher test results merged into Telegram alerts
- **B6**: GARCH(1,1) inline in `compute_layer9_score` (layer9_statistical_edge.py) as ±adjustment (like RND, not weighted component — avoids weight-sum distortion on failure); bg scanner GARCH block moved BEFORE `batch_layer9_scores` for audit timestamp ordering
- **B2+B5+B7 partial**: Step 2c pre-fetch in `_aiem_paper_mark_to_market()` — fetches `garch_regime_log` (vote, alpha1+beta1), `layer9_scores` (pca_factor1_var), `stat_arb_signals` (zscore) in one DB round-trip; enriches pos_entry; `_rules_mtm_decision()` reads via `pos.get()` (no-phone-home preserved); 3 exit + 1 hold branches added
- **Group C**: `discovered_candidates` added to Layer9 bg scanner `_l9_sources` (14-day, status != rejected, LIMIT 20)

## Blocked / Flagged

- **A2 (skew_velocity)**: Needs historical put_skew_25d time series across multiple days. `compute_layer9_score` only has a single chain_df snapshot. Fix requires a nightly job storing daily 25-delta put skew to a new column/table.
- **A3 (benchmark_comparison.py)**: NOT dead — `compare_agent_to_baselines()` (which calls `monte_carlo_random_baseline()` internally) already called from main.py:39656 inside AIEM benchmark tool.
- **A5 (literature_scanner.scan_and_save)**: Requires web search API callback never configured.
- **A7 (Volume Profile)**: EXISTS inline at main.py:49100-49115 (`_enrich_technical_signals`, stored as `tech_poc`). Not absent.
- **B1/B3/B4**: `aiem_signal_discoveries` has NO `signal_name` column (schema: id, hypothesis_text, conditions_json, p_value, oos_edge…). No mapping from `aiem_paper_trades.signal_source` to `aiem_signal_discoveries.id`. Signal decay verdict, BH-FDR rejection, and OOS overfit all require this bridge — cannot join without adding `signal_name VARCHAR` to the table.
- **Group C (AI Trades)**: `v3_decisions` already wired to paper picks at main.py:42483-42497. `discovered_candidates` human-review gate is intentional per `aiem_discovery_engine.py` docstring.

## Key structural finding
`aiem_signal_discoveries` schema (confirmed 2026-07-11) has no `signal_name` column. Any future attempt to join paper_trades signals to discoveries by name will fail silently. The bridge column must be added as: `ALTER TABLE aiem_signal_discoveries ADD COLUMN IF NOT EXISTS signal_name VARCHAR(100)` and backfilled via the tool that creates discoveries.
