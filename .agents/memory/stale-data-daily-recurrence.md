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

## Generalization: the bug class is broader than one tab

The same "yesterday-as-today" defect recurs across MANY endpoints, not just
conviction-calls. Three structural shapes, all genuine bugs:

1. **A "Today"/"current"/"live" label or toggle backed by a rolling window.**
   e.g. a pre-check/merge that says "today's data first" but queries
   `last_seen >= NOW() - INTERVAL '36 hours'` (or `'48 hours'`). At market open
   this serves a today+yesterday mix. Fix: filter to the ET calendar day.
2. **Silent today→older fallback.** Query today, then if empty silently re-query
   `NOW() - INTERVAL '1 day'` (or widen the window). Same cure as conviction:
   ET-today only, honest empty-state, older windows opt-in.
3. **In-memory cache reused across days.** An app-level cache (e.g. the unusual
   calls scan cache) gets read by ANOTHER endpoint (e.g. the AI picks) without
   checking the cache's date — so yesterday's leftover cache feeds an AI prompt
   that says "today's signals". Fix: only use the cache if its `*_cache_ts`
   falls on the same ET calendar day; else treat as empty.

**Canonical ET-today filter (server/DB TZ = UTC; `last_seen` is naive-UTC):**
`last_seen >= (date_trunc('day', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York') AT TIME ZONE 'UTC'`.
Avoid bare `CURRENT_DATE` for "today" — that is the UTC day, which is wrong in
the ET evening. To compare a Python naive-UTC timestamp's ET date:
`ts.replace(tzinfo=utc).astimezone(ZoneInfo("America/New_York")).date()`.

**Why:** the first scan of the day runs ~9:30–9:45 AM ET, so any rolling/UTC
window serves yesterday every pre-scan morning — it WILL recur on its own.

**Scoping rule — do NOT over-fix (this is the trap):** a broad audit will
massively over-flag. Most tabs are FINE and must be left alone:
- **Intentional rolling-activity windows** (dark pool, whale, put-intent, etc.
  at 24h/48h) never claim "today" — they show recent activity and usually render
  per-row dates. Converting them to calendar-today would empty them daily.
- **Intentional multi-day tabs** (congress ~90d, insiders ~30d, persistence
  ~14d, gamma-pressure default ~3d) are not bugs.
- A tab that ALREADY surfaces each row's date (badges / "Detected <date>" /
  timestamps) and has an honest empty state is NOT hiding staleness — leave it
  (e.g. etf-calls' explicit ALL-TIME/TODAY toggle, gamma-pressure's date picker
  + per-row timestamps + today-only stat).
Only fix when a "today/current/live" claim is backed by a rolling/UTC window OR
a silent older fallback OR a cross-day cache. Verify the cure at the DB level:
compare row counts of the ET-today expression vs the old window before/after.
