---
name: Small-cap morning two-stage conviction system
description: Design, sizing, options-score + double-signal additions, and the env-schema/timing gotchas for the daily small-cap morning BUY workflow in stock-scanner-api/main.py
---

# Small-cap morning two-stage conviction system

Sibling of the nano-cap morning system (see nano-morning-system.md), but for TRUE
small caps ($300M–$2B) that are OPTIONABLE. Lives in `artifacts/stock-scanner-api/main.py`.
Two-stage like nano: 9:37 ET "get ready" watch email → 9:47 ET confirmed BUY email
(reuses nano's `_nano_intraday_confirm` for the opening-15-min VWAP/volume re-check).
Stage A ranking @ 8:15 ET, Stage D grade-forward @ 16:12 ET. Separate emails/tables
from nano. Universe via Finviz `cap_small,sh_opt_option,sh_price_o2,sh_avgvol_o100`.

## Sizing (differs from nano on purpose)
- **$1,000 WORTH per name** (`_SC_DOLLARS_PER_BUY=1000`; `shares=int(1000/entry)`), 5% stop,
  winners ride, concentrate to top ~15. **Why:** small caps are more liquid and less
  violently fat-tailed than nanos, so fewer/larger positions beat nano's breadth play
  (nano is 20 @ $500). Do NOT copy nano's $500/20-name rule here.

## Two NEW score components nano can't have
- **Options-activity score (0–25):** read from STORED tables, NEVER a live option-chain
  fetch in the morning critical path (8:15 pre-market, freshest data = yesterday's
  positioning — exactly what we want). Primary = `unusual_calls_microcap_log`
  (vol_oi, prem, far_otm_sweep, last_seen); additive bonus = `call_sweep_log`.
- **Double-signal flag (+12 conviction bonus):** morning candidate that was ALSO on
  yesterday's EOD accumulation list. Join `eod_accum_picks WHERE scan_date=<prev ET
  trading day> AND COALESCE(signal_type,'accum')='accum'`, fallback to MAX(scan_date)
  within 5 days. Requires `signal_type` column — added via `ALTER TABLE eod_accum_picks
  ADD COLUMN IF NOT EXISTS signal_type TEXT DEFAULT 'accum'` at init so the join works
  even before the day's first accumulation scan.

## Env-schema gotcha (cost a debug cycle)
- `call_sweep_log` in some environments is a LEGACY schema MISSING `conviction` (and
  other newer cols) — `CREATE TABLE IF NOT EXISTS` never backfills columns on an existing
  table. **So `_sc_options_points` must depend ONLY on columns guaranteed everywhere
  (`vol_oi_ratio`, `premium`), never `conviction`.** The micro-log query and the
  call-sweep query are in SEPARATE try/except blocks so sweep schema drift can't wipe the
  primary micro-log options points. **How to apply:** before using any call_sweep_log
  column in a query, confirm it exists in BOTH dev and prod, or guard it.

## Options lookback is 5 calendar days, NOT latest-session-only (deliberate)
- `_sc_options_points` aggregates `[today-5d, today)`, not strictly the single most-recent
  completed session. **Why:** institutional options positioning builds over multiple days;
  a single-session snapshot is too sparse and breaks on holidays/missed scans. This is an
  intentional deviation from the original "latest completed session only" spec.

## Guards / conventions (mirror nano; do not regress)
- Floor guard before DELETE: `_run_sc_morning_ranking` only DELETE+rewrites today's
  candidates if `len(results) >= max(25, universe//10)`; below that → keep prior rows.
- **Zero candidate rows for today = the scan DIDN'T COMPLETE, not "nothing qualified"**
  (a successful scan always writes ≥ floor rows since every name with usable history gets
  a row regardless of conviction). Both empty-state emails say "scan didn't complete" —
  do NOT word them as "nothing qualified."
- Buy run is authoritative: `_send_sc_buy_email` DELETEs today's `sc_morning_picks` before
  re-inserting so a shrunken rerun can't leave stale picks to be graded.
- Dedicated `app._sc_morning_lock` (do NOT share the conviction scan lock).
- Ranking @ 8:15 ET is staggered 15 min after nano's 8:00 to avoid a yfinance collision.
- `_nano_admin_ok` was renamed `_admin_ok` (now the shared fail-closed gate for both nano
  and SC POST routes); needs `ADMIN_TOKEN` env + `X-Admin-Token`/`?token=`. Scheduler calls
  the functions directly (not over HTTP), so it's unaffected by the token.
