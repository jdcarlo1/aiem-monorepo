---
name: Market-open spinner root cause and fix pattern
description: Why tabs spin at 9:30 AM ET and the pattern that stops it
---

## Rule
Every endpoint that may call Yahoo (directly or via a helper) MUST check
`_yf_breaker_open()` and return from cache/DB immediately if true.

## Why
At market open (9:30-9:45 ET), APScheduler fires 8+ concurrent jobs → yfinance
saturates → Yahoo returns 429/rate-limit → breaker trips → BUT endpoints that
lack the guard still try live fetches, hang 18s each, Flask workers stall,
user sees spinners forever.

## Endpoints fixed (June 2026)
morning-runners, eod-accumulation, multi-signal, earnings-calendar,
insider-radar, ai-short-calls — all now return cache/DB in <0.5s when breaker open.

## Verification pattern
Endpoints that do NOT call Yahoo (darkpool=FINRA CDN, outcomes=DB only,
ai-short-calls=OpenAI+DB) should NOT get the breaker guard — it would make
them return empty even when Yahoo isn't the issue.

## Insider/trades timeout
Set to 2.5s (not 4.0s) so cache fallback fires faster under load.

## Post-fix smoke test result
24/24 endpoints OK, 0 slow(>3.5s) after breaker guards added.
