---
name: Discovery pipeline promotion wiring
description: loop_a_research now calls _mkt_tool_validate_oos + _mkt_tool_save_discovery for qualifying market findings
---

## What was wired (2026-08-04)
`loop_a_research` in `main.py` (near line 47711) now has a full promotion block inserted between
the `aiem_research_insights` save and the `save_research_model` call.

**Before:** `discovery_saved=False` hardcoded; `_mkt_tool_save_discovery` was never called from the automated battery.

**After:** For each significant market finding (source=="market"):
1. Pre-filter: skip if WR < 54% or n < 200 (saves expensive DB round-trip)
2. Call `_mkt_tool_validate_oos(conditions=…, horizon=…)` — 60/40 train/test split
3. If `oos_validated=True` and `oos_edge > 0`:
4. Call `_mkt_tool_save_discovery(…)` with all 4 gate fields populated
5. Log via `_aiem_log_tool_call` at each step
6. `discovery_saved=(_ra_discovery_count > 0)` on session close

SHA of main.py post-fix: 1c7bce32ac7c77341dc9d8212d69862a206c0bf2cf4a03f5addc5a0acc7b2ea6

**Why options findings are excluded:**
Options battery uses raw SQL-string conditions (not dict) — incompatible with `_mkt_parse_conditions`.
Options findings only promote manually via AIEM LLM session (mkt_validate_oos → mkt_save_discovery).

**Aug 1 backfill blocker:**
The 8 stranded indicator-grid patterns (RSI, BB, Williams, MFI conditions) use `polygon_indicators_daily`
column keys that `_mkt_parse_conditions` does not handle. They cannot be inserted via the
production gate without extending `_mkt_parse_conditions` to cover indicator columns, or running
a separate OOS harness. This is a separate task — do not bypass the oos_edge gate to backfill them.

**How to apply:**
Never add `discovery_saved=False` hardcoded to any new research loop. Always wire the promote block.
