---
name: Layer9 universe build bugs
description: Three bugs that kept layer9_scores at 0 rows despite the scan reporting success
---

# Layer9 Universe Build — Three Bugs Fixed

## Bug 1: Single try/except killed all 5 universe sources
All 5 SQL queries (polygon_rvol_scan, unusual_calls_log, conviction_stack_watchlist,
ai_short_calls_log, aiem_paper_trades) were inside ONE try/except block sharing ONE
psycopg2 connection. When `polygon_rvol_scan` doesn't exist (table only created by the
8:35 AM Polygon scan — missing before first run of the day), the exception aborted ALL
other queries → empty universe → scanner always skipped on fresh deploys.

**Fix:** Each source gets its own isolated try/except + its own connection.

## Bug 2: Yahoo circuit breaker gating a Tradier-only scanner
`_yf_breaker_open()` was used to gate `_td_history()` calls inside `_run_layer9_bg_scan`.
During morning scan bursts, Yahoo's breaker trips → layer9 pauses 30s → if still open,
breaks the ticker loop entirely → "no histories fetched — aborting".
Layer9 uses ONLY Tradier. Yahoo breaker has nothing to do with it.

**Fix:** Replace `_yf_breaker_open()` with `_TD_BREAKER.get("tripped", False)`.
Note: `_TD_BREAKER` is a dict, not a Python object — use `.get()`, never `.tripped`.

## Bug 3: 2-3 day lookbacks empty on weekends/fresh deploys
Lookbacks were: polygon_rvol_scan=2d, unusual_calls_log=3d, conviction_stack_watchlist=3d,
ai_short_calls_log=2d. On Sunday or after a fresh deploy on Monday morning (before any
scans run), all sources return 0 tickers → empty universe.

**Fix:** Extended all lookbacks to 7 days. Added fallback liquid universe (AAPL/NVDA/TSLA/
AMD/META/etc.) when all DB sources return 0 tickers — ensures layer9 always scores
something even before market-hours scans run.

## Specialist Council admin endpoint added
`POST /stock-api/admin/run-council-now` — triggers one council run on a given ticker
(default NVDA, context candidate_entry) so aiem_specialist_council_runs can be proven
end-to-end without waiting for paper trades to cycle. macro_bias must be a float (0.0 = neutral),
not a string — _build_opinion() calls float(mb).

## Options structure scan — by-design empty
`options_structure_scan` only runs at 10:05 AM ET on market days via owner email schedule.
0 rows on weekends/nights is correct behavior. Code wiring is complete:
`_send_gex_options_alert → _aos.scan_options_structure → _aos.save_to_db → options_structure_scan`.
Admin endpoint: `POST /stock-api/admin/run-gex-options`.
