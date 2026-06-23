---
name: Unusual-calls scan vs endpoint vol_oi mismatch
description: When you change scan thresholds you must also change endpoint DB query thresholds — they are separate gates.
---

## The Rule
Every vol_oi or OTM threshold that exists in `_scan_one` / `_scan_unusual` has a **matching threshold in the endpoint's DB queries**. If you lower the scan threshold and forget the endpoint, signals get saved to the DB but filtered out before they reach the frontend.

## Locations to keep in sync
- `_scan_one` (hourly Polygon+Yahoo scan, ~line 1086): `min_voi` non-ETF
- `_scan_one` (admin EOD scan, ~line 18639): `min_voi` non-ETF (second copy)
- `_scan_unusual` (hourly Yahoo scan, ~line 17688): `min_voi` non-ETF
- `_load_todays_unusual_calls_from_db` (~line 9021): `AND vol_oi >= N`
- `unusual_calls` endpoint (~lines 17568, 17600, 17756, 17811, 17828): `AND vol_oi >= N` (5 paths: cache_only, after-hours, breaker-fallback, pre-check, stale fallback)

**Current calibrated values (June 2026):** scan `min_voi = 1.0` non-ETF, endpoint `>= 1.5`.

## OTM filter
All scan paths use `otm_pct < -15` (catches near-ITM institutional sweeps like ARQQ $25 call at -12% ITM). Previously was -5% which was too strict.

## Polygon Starter price caveat
Polygon Starter `underlying_asset.price` is NULL intraday → falls back to `/v2/aggs/ticker/{ticker}/prev` (prev-day close). On high-gap days (e.g. ARQQ +33%), OTM display is wrong (shows +16% OTM when actually -12% ITM). Signal detection is correct; display is wrong. Fix requires paid Polygon tier or a yfinance price fallback.

## Microcap endpoint key
The microcap endpoint (`/unusual-calls/microcap`) returns `{"signals": [...], "total": N}` NOT `{"hits": [...]}`. The main unusual-calls endpoint returns `{"hits": [...]}`.

**Why:** The issues were discovered when ARQQ (vol_oi=2.91) appeared in the DB after a scan but not in the endpoint — the `>= 3` endpoint filter was blocking it.
