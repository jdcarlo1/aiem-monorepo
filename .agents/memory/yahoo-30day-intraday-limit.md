---
name: Yahoo 1-min intraday 30-day retention limit
description: yfinance 1-minute intraday bars silently age out after ~30 days, breaking backtests/scans that reference a fixed historical date as "today" advances; Polygon.io 1-min aggs have no such cutoff.
---

## The problem

Any script or job that pulls yfinance 1-minute (or other short-interval)
intraday bars for a specific historical date will work fine at first, then
start silently returning empty/short data for that same date once real
calendar time has advanced past yfinance's ~30-day intraday retention
window. This is easy to miss because the failure is not an exception — the
download just returns less data (or none) for the aged-out day, which can
look like "no signal" rather than "data unavailable."

This bit a backtest that computed opening-volume from 1-minute bars for a
fixed date (e.g. Jun 1): it worked when run shortly after Jun 1, but broke
silently as "today" moved into July.

## The fix

For any historical intraday lookup that must remain valid indefinitely
(backtests, audits, re-runs), don't rely solely on yfinance intraday bars.
Add a Polygon.io 1-minute aggregates fallback (uses `POLYGON_API_KEY`) —
Polygon does not impose the same 30-day intraday cutoff, so it can serve as
the source of truth once a date ages out of Yahoo's window.

**How to apply:** any time you're pulling minute-level intraday history for
a date that isn't "recent" relative to when the script runs, check whether
Yahoo actually returned data for that date before trusting it, and fall
back to Polygon's aggs endpoint if it didn't.
