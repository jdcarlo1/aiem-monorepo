---
name: AIEM 24/7 indicator grid battery
description: Free-tier continuous technical-indicator hypothesis sweep (Loop B companion) that pauses only during the production trading window; how it gates findings and where it can silently lose data.
---

`_mkt_indicator_grid_battery()` + `_mkt_continuous_research_loop()` (main.py) run a perpetual
background thread that sweeps ~184 single/multi-indicator cells (RSI/MACD/ADX/Stochastic/CCI/
Williams %R/Bollinger/CMF/MFI/ROC/momentum/ATR/MA-distance/52wk-range) x 4 horizons
(next_day/3d/5d/10d) against `polygon_indicators_daily`, 15 cells/batch, each cell retested at
most every 10 days. It is completely separate from the paid-GPT Loop A/Loop B agents — zero
OpenAI tokens — and pauses only Mon-Fri 8:00-16:30 ET (the production scan window); it runs nights,
weekends, and holidays.

**Why it's safe despite a low per-cell n>=15 gate:** that's only a candidate screen. Every test
still goes through `_mkt_tool_test_signal` → `_mkt_log_statistical_test`, so it's bound by the SAME
self-tightening Bonferroni ledger (`_mkt_tool_required_pvalue`) as the paid agents, and promotion to
`aiem_signal_discoveries` still hard-requires n>=200 downstream. Findings that clear the screen are
written to `aiem_research_insights` as `type: "indicator_grid_finding"` — the system prompt
explicitly tells Loop A these are NOT pre-validated and must go through inverse/OOS/shadow checks
before being trusted. Treat HIGH/MEDIUM confidence labels on these rows as ordering hints only:
cross-sectional correlation (thousands of tickers, same dates) + overlapping multi-day horizons
inflate effective n and deflate p-values beyond what Bonferroni alone corrects for.

**How to apply:** if extending the grid (new indicators, more combos, shorter refresh_days), keep
routing through the shared Bonferroni ledger — never bypass `_mkt_tool_save_discovery`'s gates for
"free" findings just because they didn't cost tokens.

See also: [aiem_research_insights unique-date gotcha](aiem-research-insights-unique-date.md) — this
grid battery is one of the writers to that table and must never do a bare loop-of-INSERTs into it.
