# StockScanner EMPTY/STALE fix pass — Neon prod connected

**Date:** 2026-08-05  
**DB:** `neondb` @ `ep-spring-flower-aqxm8amx-pooler…` (417 public tables)

## Root cause

`_load_scan_cache` default lookback was **5 days**, but many last-good snapshots are from **2026-06-30** (~36 days old). Yahoo-throttle paths returned EMPTY instead of those snapshots.

## Fixes

| Change | Effect |
|---|---|
| `_load_scan_cache` default `days_back` 5 → **60** | Restores composite, multi-signal, squeeze-setup, insider-trades, etc. from June caches |
| Insider trades | DB snapshot on cold start + Yahoo breaker |
| Composite score | `composite_score_history` fallback (5.8k+ rows) |
| Convergence | DB approx from `unusual_calls_log` + `polygon_rvol_scan` |
| Earnings calendar | Serve `earnings_calendar` table (270 upcoming) |
| EOD accumulation | Last **90 days** of `eod_accum_picks` (not today-only) |
| Short squeeze | 60d cache + `aiem_squeeze_signals` (25 rows) |
| Persistence | Lookback 14 → **60** days (`signal_outcomes`) |
| Nano morning | Fall back to `sc_morning_candidates` when nano table empty |
| Market overview | Don’t hard-empty on Yahoo breaker; Tradier path + cache save |

## Still gated / cannot invent data

| Tab | Why |
|---|---|
| Paper Money | Admin token required |
| Gas Board live | Subscriber token POST |
| Quant Agent | BYOK |
| Congress | No congress table in Neon |
| ORB / some morning nano | No rows until next market-hours scan |
| Unusual puts log | Table exists but **0 rows** (scan hasn’t persisted puts yet) |

## Verify (after deploy)

```bash
curl -s "$HOST/stock-api/composite-score" | jq '.results|length, .stale, .note'
curl -s "$HOST/stock-api/multi-signal" | jq '.hits|length, .stale'
curl -s "$HOST/stock-api/insider/trades" | jq '.count, .stale, .note'
curl -s "$HOST/stock-api/earnings-calendar" | jq '.count, .stale'
curl -s "$HOST/stock-api/convergence" | jq '.results|length, .note'
```
