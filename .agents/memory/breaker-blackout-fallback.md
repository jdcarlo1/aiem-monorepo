---
name: Circuit-breaker blackout + tab auto-fallback
description: Why the fail-fast Yahoo breaker can turn partial throttling into a full data blackout, and how High Conviction / Unusual Calls auto-fall-back to recent saved names so they're never blank
---

# Circuit-breaker blackout → blank tabs, and the auto-fallback mitigation

## The trap
The shared Yahoo fail-fast circuit breaker (trips on 429/503 + 401-burst + any
curl_cffi exception, ~30s cooldown) combined with heavy concurrent background
scans can turn a *partial* Yahoo throttle into a *near-total* data blackout for
the whole session:
- Breaker opens early under the morning burst.
- While open, scans SKIP tickers (return None) instead of collecting → almost
  nothing written to `unusual_calls_log`.
- When cooldown expires, many scan threads fire real calls simultaneously → at
  least one throttles → breaker reopens immediately. Self-perpetuating "open all
  day".
- Net: a day where `unusual_calls_log` gets ~1 ticker instead of ~165, so the
  High Conviction + Unusual Calls tabs (both read `unusual_calls_log`) render BLANK.

**Why:** owner reported both tabs blank "only today, ever since last night's
changes" (the breaker was that change). Confirmed via prod read-only SQL: today
3 rows/1 ticker vs 165+ tickers on normal days; prod logs showed "circuit
breaker open" firing continuously. Same breaker-open seen in DEV logs too.

## The mitigation (band-aid, not a cure)
Both tabs now auto-show the most recent saved names when today is quiet, so they
are never blank and need no click:
- **conviction-calls**: fallback is now DEFAULT-ON (`?fallback=0` forces strict
  today-only). When today empty → 24h → 7d. Returns `window` + `stale`; frontend
  already renders a "LAST 24H/7D" badge + banner (Dashboard `windowLabel`).
- **unusual-calls**: when the live scan AND today's DB rows are sparse (`<5`,
  matching the existing `>=5` pre-check threshold), pull the most recent 7-day
  saved rows, set `stale`, show a notice banner. The frontend state type needed
  `stale?`/`note?` added.

**Critical data-integrity guard:** stale fallback rows must NEVER be passed to
`_save_unusual_calls_to_db` — its ON CONFLICT sets `last_seen=NOW()`, which would
re-stamp older rows as today's and corrupt the per-row "detected" dates. Guard is
`if all_hits and not _stale_fallback`. The stale unusual-calls cache timestamp is
back-dated so it expires in ~3 min (instead of the 15-min TTL) → fresh rows
replace stale names as soon as the feed recovers.

The conviction **snapshot** job uses its OWN today-only query and returns early
when empty, so the route's default-on fallback can never record older names as
today's snapshot. Verified safe.

## The real fix (NOT done; surfaced to owner)
Auto-fallback shows STALE names on throttle days — it does NOT restore data
collection. Real fixes, in order:
1. Breaker **half-open single-probe** (allow only ONE probe call when cooldown
   expires; fully close only if it succeeds) + lower background-scan concurrency,
   so the breaker stops blacking out collection. Riskier hot-path change —
   consult architect (`responsibility: plan`) before doing.
2. Paid full-market feed (Polygon/Alpaca) — permanent fix for the free-feed
   throttle ceiling (see `scanner-data-source-ceiling.md`).
