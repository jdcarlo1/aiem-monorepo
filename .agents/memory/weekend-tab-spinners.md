---
name: Weekend tab spinners
description: Pattern for preventing infinite frontend polling on weekends when background scan workers produce no data
---

## Rule
Any endpoint that starts a background scan worker MUST call `_intraday_scan_allowed()` before spawning the thread.
Returning `warming: true` or `refreshing: true` when market is closed causes the `useMicrocapFlow` hook (and similar
polling hooks) to poll every 7 seconds forever — "Scanning 350+ tickers…" spinner never resolves.

**Why:** On weekends/holidays, yfinance calls produce nothing, so the worker finishes with an empty cache,
the endpoint keeps returning `warming: true`, and the frontend loops endlessly.

**How to apply:**
1. At the top of the endpoint (before spawning thread): `if not _intraday_scan_allowed(): return DB fallback`
2. Return `{"stale": True, "note": "Market closed · showing last trading day's scan", ...data...}` — no `warming` key
3. DB fallback: `_load_scan_cache("scan-name", days_back=5)` covers 3-day weekend + holiday gaps
4. If no DB data at all: return empty lists with `stale: True` and a note (never `warming: True`)

Fixed in: `net_flow_microcap` endpoint (main.py ~line 32087).
