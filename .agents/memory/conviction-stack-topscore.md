---
name: TOP SCORE 8+ rebased onto L1-L8 conviction engine
description: The "TOP SCORE 8+" tab is now scored by the Smart Money Pressure (L1-L8) engine, NOT composite_scan.py. Read before touching TopScoreTab, conviction_stack_watchlist, or the universe-width seam.
---

# TOP SCORE 8+ — now driven by the L1-L8 conviction engine

The "💎 TOP SCORE 8+" tab (`TopScoreTab` in `Dashboard.tsx`, `tab==="topscore"`) is
scored by the existing **Smart Money Pressure** engine `_run_conviction_scanner`
(`total_pts >= 8` = EXTREME), NOT the technical `compute_score`/composite pipeline.

**Why:** the composite/technical score and the money-pressure engine disagreed;
the user wanted the headline tab ranked by the smart-money engine. The composite
pipeline (`composite_scan.py`, `composite_watchlist`, COMPOSITE tab) is left
**dormant but intact** — do not delete its tables/routes. (`composite-topscore.md`
documents that dormant pipeline; it no longer describes the TOP SCORE tab.)

## Backend (main.py)
- Table `conviction_stack_watchlist` (UNIQUE(snap_date,ticker)); logs ONLY the
  `total_pts>=8` cohort. `snapshot_conviction_stack()`, `fill_conviction_stack_outcomes()`,
  `get_conviction_stack_track_record(days)`; routes `/conviction-stack-snapshot/trigger`
  (`?sync=1` inline), `/conviction-stack-outcomes/trigger`, `/conviction-stack-track-record`.
- Scheduler: snapshot 16:50 ET (after OI snapshot 16:30), outcomes 9:52am + 17:05 ET.
- Track-record horizon = same convention as composite: entry at **next-session OPEN**
  after snap_date, exits at **close of future index 4/9/14/19** (1/2/3/4 wk). STOCK returns.

## Universe-width seam (Option A free → Polygon later, NO tab rewrite)
- `CONVICTION_STACK_MAX` (module const, currently 60) is the **single knob** for
  universe width. BOTH the live `/conviction-stack` endpoint AND
  `snapshot_conviction_stack()` use it, so displayed universe == logged universe.
- `max_tickers` in `_run_conviction_scanner` caps BOTH the output
  (`results[:max_tickers]`) AND the heavy L4-L8 fetch pool (`active[:max_tickers*3]`),
  so too small a value *suppresses scores* (fewer layers fire → names can't reach 8),
  not just truncates the list. Prior bug: endpoint used 25 while snapshot used 60.
- Responses carry `source` (e.g. `"free_yfinance"`) + `universe_count`. Frontend binds
  the universe stat to `data?.universe_count ?? results.length` and consumes only
  conviction shapes (`total_pts`/`layers`/`meta`/`source`/`universe_count`) — so a paid
  feed widening coverage needs only backend changes (raise/remove `CONVICTION_STACK_MAX`
  or compute an uncapped `universe_count`), no tab rewrite.

## On-demand single-ticker scoring (force_tickers)
- The heavy layers L4-L6 (short interest, dark pool, float pressure — all live
  yfinance) only run on the `active` set = names that already have an L1/L2/L3
  (OI / gamma / charm) signal. So a name whose only footprint is a sweep (L7) or
  dark pool shows a deceptively low score (e.g. SMCI/TLN at ~2.0) because L4-L6
  were never computed for it.
- `_run_conviction_scanner(force_tickers=[...])` seeds those tickers into
  `scores` + `active` so ALL 8 layers run, and keeps them in the output past the
  cap / 1.0-pt floor. Route `GET /conviction-stack/score/<ticker>` wraps it.
- **Why:** lets any ticker be scored on demand (user asks "score X") without it
  having to first appear in the OI/charm/gamma tables. Default `force_tickers=None`
  leaves the normal ranked path byte-for-byte unchanged.

## Idempotency (must preserve)
- `snapshot_conviction_stack()` is same-day idempotent. On a **non-empty** cohort it
  deletes same-day rows not in the keep set, then upserts.
- On an **empty** cohort it must DELETE all `WHERE snap_date=today` rows BEFORE returning
  `skipped_empty_extreme`, or a same-day rerun that finds zero 8+ names leaves stale 8+
  rows that pollute the track record. The engine-error path returns `ok:False` *before*
  any DB touch, so only a *legitimate* empty cohort prunes (a transient engine crash
  won't wipe good data).
