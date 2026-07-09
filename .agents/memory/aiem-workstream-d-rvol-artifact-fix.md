---
name: Workstream D RVOL data-artifact bug (fixed 2026-07-09)
description: Why AIEM Independent Picks (Workstream D) was sending garbage tickers like $SHPH/$MIGO with RVOL up to 11,254x, and the two-part fix.
---

## Root cause (two compounding bugs, both in `_run_aiem_independent_pick_scan` / `_aiem_indep_tool_stock_universe` in main.py)

1. **No RVOL ceiling on the candidate universe.** `polygon_market_daily.rvol` is a raw
   volume-ratio with no independent liquidity baseline to sanity-check it against. Thin/
   halted/reverse-split tickers routinely show RVOL in the hundreds or thousands (verified
   live: `USCA` at 11,254x, `CHYG` at 4,577x, `AAAP` at 1,184x on the same scan date) — these
   are data artifacts, not genuine conviction, but nothing filtered them out.
2. **Scoring formula saturated at the RVOL extreme.** `min(rvol,10.0)*1.5` alone hits 15,
   which by itself exceeds the final `min(10.0, score)` clip for ANY rvol >= 6.67x. Every
   artifact-tier name scored an identical "10.0/10" with zero differentiation from
   close_strength/gap/momentum/range — the formula couldn't tell a real setup from a glitch.

## Fix
- Added `max_rvol=40.0` param + SQL gates (`rvol <= 40`, dollar-volume floor $1M->$3M, added
  absolute share-volume floor `volume >= 300000`) to `_aiem_indep_tool_stock_universe`.
- Rebalanced score weight: `min(rvol,6.0)*1.0` (was `min(rvol,10.0)*1.5`) so close_strength/
  gap/momentum/range actually move the ranking again.
- Verified against live `polygon_market_daily` data: old top-15-by-RVOL list was 100% data
  artifacts (RVOL 111x-11,254x); new filtered list tops out at RVOL ~40x with real
  differentiation in final confidence_score (7.35-10.0 range, not a flat wall of 10s).

## Known remaining gap (not yet fixed, same session)
The options leg of the same function (`kind="options"`) has the identical saturation pattern
(`min(voi,20.0)*2.0` maxes at 40, saturating the same 10.0 clip) — not fixed, deprioritized
since the user's complaint was specifically about stock picks.

Also still open (unrelated bug, found same session): `aiem_telegram_notifier.py`
`_fetch_todays_picks` silently falls back to the unrelated `aiem_process_predictions`
(nano-cap system) table when Workstream D produces 0 rows for the day, and sends those under
the same "AIEM Independent Picks" branding without disclosing the source switch.

See also `aiem-independent-picks-telegram.md` for the overall Workstream D architecture.
