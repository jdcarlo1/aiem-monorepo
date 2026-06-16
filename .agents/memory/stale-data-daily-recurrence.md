---
name: Stale dashboard data (daily "yesterday's names") root cause
description: Why StockScanner dashboard tabs showed yesterday's data every morning, and the two-part structural fix
---

# Stale dashboard data recurring every day

**Symptom:** Dashboard tabs (notably "High Conviction Calls") show yesterday's
names every morning. Reported as happening *every single day*, each needing a
"fix" + republish.

**There were TWO independent causes — fixing only one leaves it recurring:**

1. **Backend silent fallback (the real daily driver).** The conviction-calls
   endpoint queried today, but if today was sparse it *silently* substituted
   yesterday's window and worse, threw away today's 1–4 names. The first scan
   of the day doesn't run until ~9:30–9:45 AM ET, so every pre-scan morning the
   endpoint served yesterday as if it were today. This is structural and
   time-of-day driven — it WILL recur daily on its own.
   **Fix:** show today whenever there is ANY today data; never auto-substitute
   an older window. Older windows are opt-in only (a `fallback` query param /
   "Show last 24h" button). When today is empty, return an explicit empty-state
   note explaining the scan schedule instead of old names.

2. **No cache control (compounding).** Client did a bare `fetch()` and the
   Flask API sent no `Cache-Control`, so the browser/proxy served stale GET
   responses. **Fix:** client sends `cache:"no-store"`; Flask `after_request`
   sets `no-store` on every `/stock-api` response.

**Why past fixes failed:** restart API / force rescan / hard refresh / redeploy
only clear the symptom for that day. They change neither the fallback semantics
nor the cache headers, so the next morning it returns. The cure must remove the
silent fallback AND add cache headers — not clear state.

**How to apply:** For any "shows old/yesterday's data" report: (a) confirm the
response is today-scoped and does not silently fall back to an older window when
sparse; (b) confirm `curl -I` shows `Cache-Control: no-store`; (c) confirm the
call routes through `fetchJson`. Note raw `fetch("/api/...")` calls hit a
different backend NOT covered by the `/stock-api` hook. There is NO service
worker in this app.
