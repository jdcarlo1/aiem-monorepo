---
name: Grinder-scan DB indexes
description: Two CONCURRENT indexes that fixed the grinder-scan query timeout (18s→0.49s) and the statement_timeout/thread-deadline alignment rule.
---

## The rule
Any heavy query (>2s) run inside a background thread must have its `statement_timeout` match the thread deadline exactly. A mismatch creates a zombie-query window: the thread exits but Postgres keeps running the query until the DB timeout fires.

**Why:** grinder-scan had thread deadline=5s, statement_timeout=8s → 3-second zombie window on every timeout.

**How to apply:** set `options="-c statement_timeout=<N>ms"` on the psycopg2 connection where N = thread `timeout` in ms.

## The indexes (created 2026-06-29, CONCURRENTLY so non-blocking)

### idx_pmd_scan_vol_price
```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pmd_scan_vol_price
ON polygon_market_daily (scan_date DESC, volume, close_price)
```
- Covers the WHERE clause `scan_date >= ..., close_price BETWEEN 8 AND 300, volume >= 300000`
- Without it: Parallel Seq Scan on 1.8 GB table (~420K rows, 18s)
- With it: planner can use index to eliminate ~95% of the table before touching heap
- Took 18s to build (table is large)

### idx_ucl_ticker_firstseen_prem
```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ucl_ticker_firstseen_prem
ON unusual_calls_log (ticker, first_seen, prem)
```
- Covers the JOIN filter: `ticker = f.ticker AND first_seen >= ... AND prem >= 50`
- Without it: Bitmap Heap Scan on unusual_calls_log using the unique (ticker, strike, expiry) key, then heap fetch for first_seen/prem filter
- With it: all three predicates satisfied at the index level, no heap fetch needed
- Took 0.2s to build (table is 2.8 MB, 10K rows)

## Result
Real execution time after indexes: **0.49s** (was >6s → endpoint timeout → tab spinner)

## Don't confuse planner cost with real time
EXPLAIN shows the query plan's estimated cost (in Postgres cost units), not milliseconds. The planner correctly chose Parallel Seq Scan over idx_pmd_date because it was reading a large fraction of the table — but the composite index gives it a better option.
