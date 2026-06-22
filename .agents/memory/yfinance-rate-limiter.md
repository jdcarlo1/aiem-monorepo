---
name: yfinance global rate limiter
description: Token bucket rate limiter wired into curl_cffi patch — the permanent fix for Yahoo throttling / circuit breaker stuck open
---

## Rule
All yfinance HTTP calls to Yahoo are globally capped at **3 req/sec** via `_YF_RATE_LIMITER` (token bucket). This runs inside the `_cffi_patched_request` monkey-patch, so EVERY yfinance call from every job goes through it — not just `_scan_one`.

## Why
The root cause of daily "spinning tabs" was that multiple background jobs (unusual calls scan + cache warmer + options warmer) all called yfinance concurrently with no throttle, collectively flooding Yahoo and triggering IP-level rate limiting. The circuit breaker then opened and stayed open all day because Yahoo's IP throttle persisted longer than the 60-second breaker cooldown.

## How to apply
- `_YF_RATE_LIMITER = _YFRateLimiter(calls_per_sec=3.0)` defined just below `_YF_BREAKER_LOCK` (~line 233)
- `_YF_RATE_LIMITER.acquire()` added inside `_cffi_patched_request`, after the breaker check, before the actual HTTP call (~line 400)
- If Yahoo throttling returns, do NOT reduce calls_per_sec below 1.0 — instead look for a rogue job bypassing the curl_cffi patch (e.g., using `requests` directly)

## Supporting changes made at same time
- Unusual calls scan: 16 slots every 30 min → **8 hourly slots at :05 past the hour** (9:05, 10:05, 11:05, 12:05, 13:05, 14:05, 15:05, 16:00)
- Cache warmer: every **90 min** (was 15 min) — was the biggest single contributor to Yahoo floods
- Circuit breaker cooldown: **60s** (was 180s) — shorter recovery since rate limiter now prevents re-trips
- Breaker trip logic fixed: exception handler now properly sets `state="open"` with lock (was only setting `until` without the lock or state change)
- Admin endpoint: `POST /stock-api/admin/reset-breaker?token=<ADMIN_TOKEN>&scan=1` — force-closes breaker and optionally triggers immediate scan
