---
name: Conviction-stack cold-start behavior
description: Why conviction-stack returns 202 on prod cold start, how the fix works, and what the snap_date staleness means
---

# conviction-stack cold-start

## The bug (fixed)
Two `psycopg2.connect()` calls in `conviction_stack_endpoint()` used `connect_timeout=2`.
Under prod startup load (25 pool connections claimed simultaneously), the direct TCP+SSL
handshake took >2s → inline DB fallback timed out → fell through to 60-90s live scan → 202.

**Pool exhaustion is NOT the cause.** The inline fallback uses direct `psycopg2.connect()`,
not the pooled helper. The pool's max=25 is irrelevant.

**Fix:** Both connections raised to `connect_timeout=10`. Same fix applied to 5 other
`connect_timeout=2` locations found across main.py.

## The data gap (fixed)
`_bg_stk()` set `app._cs_stk_cache` but never called `snapshot_conviction_stack()`.
Only the EOD scheduler job saved to `conviction_stack_watchlist`. So every cold start
served whatever the EOD job last wrote — potentially days old.

**Fix:** `_bg_stk()` now calls `snapshot_conviction_stack(precomputed=_results)` after
the live scan completes. Persists 8+ pt signals to DB so next cold start is fresh.

## The min_pts=8.0 gate
`snapshot_conviction_stack()` only saves tickers scoring ≥ 8.0 pts. On Sunday (thin
signals), all tickers score 5-7 pts → `skipped_empty_extreme` → DB unchanged. This is
**correct behavior by design**. The fix matters on real trading days.

## Timing logs added
```
[conviction-stack] inline-fallback connect+query: Xms, rows=N, snap=YYYY-MM-DD
[conviction-stack] bg-phase1 connect+query: Xms, rows=N, snap=YYYY-MM-DD
```
Use these to monitor real connect times vs the 10s budget on prod.

## snap_date staleness
- Dev DB: 1 row, snap_date=2026-06-18 (dev scheduler doesn't run continuously)
- Prod DB: should have Friday Jun 27 data (EOD job runs every trading day)
- Weekend behavior: DB stays at last-trading-day snapshot; live scan refreshes after 60s

## How: Working correctly after fix
Cold start → inline DB fallback (3-4ms) → serves stale-200 immediately →
bg scan starts (60s delay) → runs live scan → if 8+ pt tickers found, saves to DB →
next cold start serves that data immediately.
