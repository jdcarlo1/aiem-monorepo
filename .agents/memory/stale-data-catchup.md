---
name: Stale data catch-up fixes
description: Why tabs show old data after mid-day deploys, and the three fixes applied
---

# Stale data after mid-day deploy

## The problem
- Production server restarts on every deploy
- If deployed mid-day (e.g. noon), all morning scheduled scans (9:05, 10:05, 11:05 AM) are missed
- Old microcap endpoint had `days_back=7` default → returned last week's data with `stale=False`
- No catch-up mechanism existed

## Three fixes applied (June 22 2026)

### 1. Microcap days_back default: 7 → 1
`unusual_calls_microcap` endpoint: default `days_back` changed from 7 to 1 (today ET only).
Weekend auto-widens to 5 days so Friday data stays visible Sat/Sun.

### 2. Post-fetch stale date check (belt-and-suspenders)
After fetching rows, if `_intraday_scan_allowed()` is True and the most recent
`last_seen` is not from today ET, `rows` is cleared → stale-fallback path fires
and returns 14-day data with `stale=True` + note. Handles explicit `?days=7` calls.

### 3. Startup catch-up scan
Added `_startup_catchup()` thread that fires 90s after scheduler starts:
- Checks if today's data exists in `unusual_calls_log` and `unusual_calls_microcap_log`
- If missing and it's a weekday 9 AM–5 PM ET, runs catch-up scans
- Unusual calls first, microcap 30s later (staggered to avoid concurrent Yahoo hammering)
- Logged as `[startup_catchup]`

**Why:** Mid-day deploys are common; without this the server silently serves stale data all day.

**How to apply:** This is already in place. If adding new scan types with daily tables,
add a similar check in `_startup_catchup()` for those tables too.
