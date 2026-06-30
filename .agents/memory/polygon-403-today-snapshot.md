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

**Open item (not yet fixed):** `_aiem_get_snapshot` is still called unguarded inside
`_aiem_grade_predictions` / `_grade_t3_t5` for T1 grading price lookups and will 403 every time
it's invoked on a still-open prediction. It silently logs `Polygon error ... 403` and the ticker
is skipped (graded=0 for that ticker), so grading quietly under-counts rather than crashing — but
it means daily P&L grading may be missing rows. Needs the same Yahoo/Tradier-quote fallback as the
missed-runner job; out of scope for the 4:30 PM retime/merge task that discovered it.
