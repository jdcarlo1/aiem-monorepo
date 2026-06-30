---
name: Polygon 403 on same-day/live endpoints (Starter plan restriction)
description: Polygon grouped-daily for TODAY and the live single-ticker snapshot endpoint both 403 on the current plan; historical dates work fine. Drives the Yahoo-screener fallback and explains the never-received 4:45 PM report bug.
---

Two distinct Polygon endpoints used by `aiem_autonomous.py` are blocked by the current plan when
the date/data is "live" (today), but work fine for historical dates:

- `/v2/aggs/grouped/locale/us/market/stocks/{date}` (grouped-daily) → 403 `NOT_AUTHORIZED` for
  today specifically; historical dates return 429 (rate-limit) instead, never 403 — confirms it's
  a plan gate on same-day data, not a general outage.
- `/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` (live single-ticker snapshot) → also
  403, used inside grading (`_aiem_get_snapshot`) for T1 current-price lookups. This is a second,
  separate consumer of the same plan restriction — confirmed via memory `polygon-yahoo-migration.md`
  ("NOT real-time snapshot").

**Why this matters:** `aiem_missed_runner_analysis()`'s `big_movers` list was always empty in
production because `_aiem_get_grouped_daily(today)` always returned `[]`, so the
`if big_movers:` Telegram-send guard never fired — the user never received the 4:45 PM report,
silently, for an unknown length of time. This was a pre-existing bug, not a one-off.

**How to apply:** `_aiem_get_today_movers_yahoo()` (Yahoo predefined screeners: day_gainers,
most_actives, small_cap_gainers, aggressive_small_caps) is the proven same-day substitute — wire
it as a fallback whenever Polygon grouped-daily/snapshot returns empty for *today*, the same
pattern already used in `artifacts/stock-scanner-api/main.py`'s `_fetch_market_movers`. Do not
assume a 403 means "transient/down" — for these two endpoints it is a permanent plan gate that
will recur every single day until the plan is upgraded or the call is removed.

**Fixed 2026-06-30:** added `_aiem_get_quote_fallback(ticker)` (Yahoo `v8/finance/chart` endpoint,
returns the same `{day:{o,c}}` shape as `_aiem_get_snapshot`) and wired it as a fallback inside both
`_aiem_grade_predictions` and `_grade_t3_t5` whenever the Polygon snapshot 403s/returns no price.
Confirmed live: all 5 of a day's predictions that were previously stuck ungraded (misleadingly
logged as "nothing to grade today") graded correctly via the fallback on first run. Apply the same
pattern to any other unguarded `_aiem_get_snapshot()` call site before assuming Polygon will ever
return live same-day data on this plan.
