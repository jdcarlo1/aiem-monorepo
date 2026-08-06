# Short Calls Record — fresh scanner track

Date: 2026-08-06  
Branch: `cursor/stockscanner-full-tabs-live-5ace`

## Request

Clear the SHORT CALLS RECORD tab so tracking starts fresh from scanner-ranked
indicators (not the old OpenAI-era thesis picks).

## What was in the live table (Neon)

- **544** rows in `ai_short_calls_log` (`2026-06-08` → `2026-08-05`)
- Theses were OpenAI-style institutional-premium copy; **0** rows matched
  “Scanner-ranked / not OpenAI”

## Action (prod Neon)

1. Copied all 544 rows → `ai_short_calls_log_archive_pre_scanner_20260806`
   (with `archived_at`, `archive_reason`)
2. `DELETE` from live `ai_short_calls_log` → **0** rows (table kept; no DROP)

**Environment note:** Helium/dev (`HELIUM_URL` host `helium`) did not resolve in
this agent environment — **single-environment check, other environment unverified.**

## Going forward

- AI Short Calls already ranks with `_deterministic_short_call_picks`
  (`ranking_mode: scanner_signals`, OpenAI ranking disabled)
- Auto-log: weekdays **10:15 AM ET** + on regenerate
- Saves use Eastern `trade_date`; deterministic picks now include a breakeven
  estimate for expiry WIN grading
- Tab empty-state explains the fresh track; API returns
  `track_cohort=scanner_signals`, `track_reset_at=2026-08-06`

Archive can be queried anytime; restore would be an explicit INSERT…SELECT if needed.
