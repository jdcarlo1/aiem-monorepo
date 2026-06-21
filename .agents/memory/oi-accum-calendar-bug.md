---
name: OI accumulation calendar-date bug
description: _get_oi_accumulation_signals used calendar arithmetic (today-N) causing 0 results on weekends and after gaps
---

## The bug
`_get_oi_accumulation_signals(days_back=1)` computed:
```python
today = _et_today()        # e.g. Sunday June 21
day1  = today - 1 day      # June 20 — no snapshot exists
day2  = today - 2 days     # June 19 — no snapshot exists
```
On any weekend or after a holiday/server-restart gap, the requested dates didn't exist in `oi_daily_snapshot` → JOIN returned 0 rows → always 0 signals.

## Fix (two parts)
1. **Use actual DB dates** — fetch the N-th and (N+1)-th most recent snapshot dates instead of calendar arithmetic:
```python
cur.execute("SELECT DISTINCT snapshot_date FROM oi_daily_snapshot ORDER BY snapshot_date DESC LIMIT %s", (days_back+1,))
dates = [r[0] for r in cur.fetchall()]
day1 = dates[days_back - 1]
day2 = dates[days_back]
```

2. **Auto-fallback** in the endpoint — if `days_back=1` returns 0 signals, try `days_back=2` automatically. This handles the case where the two most recent snapshots have no ticker overlap (rotating priority pools).

## Zero ticker overlap problem
The snapshot universe is built from `unusual_calls_microcap_log` (changes daily) + static pool. When the priority pool changes day-to-day, consecutive snapshots cover different tickers → JOIN returns 0 shared ticker/strike/expiry rows.

**Fix:** Always add the previous snapshot's tickers to the current day's priority pool:
```python
cur.execute("SELECT DISTINCT ticker FROM oi_daily_snapshot WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM oi_daily_snapshot)")
_prev_tickers = [r[0] for r in cur.fetchall()]
priority = list(dict.fromkeys(priority + _prev_tickers))
```

**Why:** Consecutive-day OI comparison requires the SAME tickers to appear in both snapshots. Without this, even a correct date query returns 0 signals.
