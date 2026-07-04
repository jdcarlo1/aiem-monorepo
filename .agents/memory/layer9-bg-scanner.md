---
name: Layer9 24/7 background scanner
description: Architecture and scope of the continuous Layer9 statistical edge scanner wired July 4 2026
---

## What was built
- `layer9_scores` table (ticker, computed_at, scan_date, statistical_score, regime, hurst_raw, vpin_raw, jump_detected, entropy_score, tail_score, vrp_score, amihud_score, error). UNIQUE constraint on (ticker, scan_date).
- `_init_layer9_scores_table()` in `_DEFERRED_INITS`.
- `_run_layer9_bg_scan()` — builds universe from polygon_rvol_scan + unusual_calls_log + conviction_stack_watchlist + ai_short_calls_log + open aiem_paper_trades; calls `_td_history` (Tradier) + `batch_layer9_scores`; upserts to DB.
- APScheduler `IntervalTrigger(hours=2)` job `layer9_bg_scan` — 24/7, no external API cost.
- Startup daemon thread fires `_run_layer9_bg_scan()` 3 minutes after boot.

## Scope — explicit inclusions and exclusions
**Feeds:**
1. `_aiem_paper_pick_candidates()` — source #9, score ≥ 65 within 6 hours, adds as "layer9_stat" source. Wired into 9:42 AM ET daily cron.
2. `_bg_aisc()` (AI Short Calls tab) — cache-first read from layer9_scores (4-hour window), live fallback for misses.

**Does NOT touch:** High Conviction, Unusual Calls, Call Sweeps, End of Day Sweeps, Calls/Bulls tab.

## AI Medium Calls
Does not exist in the codebase as of July 4 2026. User requested it — needs to be built as a new endpoint + frontend tab when they want it.

**Why:** User said "until I see a good couple months of really good data" before expanding scope. Conservative rollout.

## Score normalization into paper trades
`_l9_score = float(statistical_score) / 5.0` → maps 65→13, 80→16, 100→20 (consistent with other sources like `aiem_ai` which uses 10-15).
