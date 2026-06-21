---
name: Insider Radar earnings-lookup timeout
description: insider-radar endpoint hung on first call due to unbounded yfinance executor; how it was fixed and why
---

## Rule
The `/stock-api/insider-radar` GET endpoint fetches earnings dates for every unique ticker seen in the last 90 days using `ThreadPoolExecutor(max_workers=12)` and `ex.map(_earn_90d, ...)`. Without a timeout this blocks 30s+ on a cold cache.

**Fix applied:** `ex.map(_earn_90d, unique_by_prem, timeout=3.0)` wrapped in `try/except Exception: pass`. Earnings proximity is only a 0-20 point bonus in the suspicion score — the other 80 points (rarity, premium, vol/oi) are computed from DB data alone.

**Why:** The tab auto-loads on mount (`useEffect(() => { load(); }, [])`). Any hang = infinite spinner for the user.

## How to apply
Any future change to `insider_radar()` that adds more yfinance calls in the executor must also include `timeout=N` on `ex.map`. The 45-min cache (`_insider_radar_cache`) means only the FIRST call after restart is at risk.
