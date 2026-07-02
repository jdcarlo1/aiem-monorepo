---
name: Discovery outcome tracker retestability gap
description: Why only a minority of aiem_signal_discoveries rows can be mechanically re-tested by Module 1 (Outcome Tracker), and how it handles the rest.
---

`aiem_signal_discoveries.conditions_json` is NOT one consistent schema — it holds
output from several different discovery mechanisms built over time (grid
battery single-row tests, S7c-style multi-day pattern backtests, indicator-lag
delta signals, the Washout Ignition multi-stage sequence). Only condition
dicts whose every key is `{field}_min`/`{field}_max` AND maps to
`_MKT_SAFE_COLS`/`_MKT_INDICATOR_COLS` can be re-run by the generic
`_mkt_parse_conditions` → `_mkt_tool_test_signal` pipeline.

**Why:** multi-day pattern definitions ("prior day", "inside day") and
indicator-lag/delta features require row-over-row window logic that a
single-row WHERE clause can't express, and the raw threshold dicts (id 7/8/9
as of 2026-07-02) don't even use the `_min`/`_max` suffix convention at all.

**How to apply:** Module 1 (`_mkt_check_discovery_outcomes` in main.py) checks
per-discovery key-mappability and writes an honest `retestable=False` row with
a `skip_reason` listing the exact unmapped keys, rather than approximating or
skipping silently — never fake a realized win rate for these. As of
2026-07-02, only 1 of 9 validated discoveries (id=6, simple rvol/close_strength
single-row condition) was mechanically retestable this way. Any future Module
2/3 work (decay analysis, variant generation) must either extend
`_mkt_parse_conditions` to support multi-day/lag patterns per discovery type,
or explicitly scope itself to the retestable subset only — don't assume all
rows in `aiem_signal_discoveries` are re-testable.
