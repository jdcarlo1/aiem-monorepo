---
name: polygon_market_daily EOD date bug in options scheduler
description: Both seed and exec in aiem_options_scheduler queried polygon_market_daily with today's scan_date — which never exists (it's EOD data). Fix pattern documented here.
---

## Rule
`polygon_market_daily` is populated with EOD data from the **previous** trading day. It NEVER has today's date on the same calendar day. Any query using `WHERE scan_date = today` against this table will return 0 rows every single morning.

## Where this bit us
Two places in `aiem_options_scheduler.py`:

1. **`seed_daily_candidates()`** — had `INNER JOIN polygon_market_daily p ON p.ticker = o.ticker AND p.scan_date = o.scan_date` where `o.scan_date = today`. Join produced 0 rows → seeded=0 → missed-seed recovery silently did nothing.

2. **`execute_pipeline_job()` Stage 1** — had `WHERE ticker=%s AND scan_date=%s` with today's date → `pmd=None` → raised `"missing Polygon/OSS data"` → FAILED for all 5 jobs.

## Fix applied
1. Seed: `JOIN ON p.scan_date = (SELECT MAX(scan_date) FROM polygon_market_daily)`
2. Exec Stage 1: `ORDER BY scan_date DESC LIMIT 1` — always use most recent available row.

**Why:** The join/fetch is only needed to confirm the ticker is in Polygon's universe (no columns from `p` are selected in the seed). Using MAX(scan_date) or LIMIT 1 DESC is always correct here.

## How to apply
Any new query against `polygon_market_daily` that needs "today's" data must use the most recent available date, not `CURRENT_DATE`. Add a comment: `-- EOD data; never has today's date`.
