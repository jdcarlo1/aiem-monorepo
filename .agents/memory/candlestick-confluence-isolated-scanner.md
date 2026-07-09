---
name: Candlestick Confluence isolated scanner
description: User-provided candlestick_backtester.py (11 patterns + vol/support/RSI confluence) was built as a fully isolated pipeline, not injected into any live scoring loop.
---

User attached `candlestick_backtester.py` with a "wiring directive" asking to embed `get_ranked_signals()` into a live scoring/ranking loop (Conviction Stack or Nano Morning Ranking). This conflicted with the [AIEM signal isolation rule](aiem-signal-isolation-rule.md). Flagged the conflict and asked the user; user chose: build it as a completely separate, isolated pipeline — own DB table, own website tab, own Telegram alert, explicitly excluded from any scoring/ranking loop and from the AIEM tool map.

**Why:** this codebase has a hard-won convention (many prior incidents) that unvalidated signals must never silently enter a live scoring path — they get their own tab until proven statistically.

**How to apply:** if the user later attaches another new backtester/signal script with a directive to "wire it into" scoring, treat that as a live-injection request requiring the same clarification, not an automatic action.

Implementation pattern used (reusable template for the next isolated signal):
- Scan function reads `polygon_market_daily`, computes patterns/filters in pandas per-ticker, returns descriptive results only (no fabricated win-rate for this app's own data).
- Own Postgres table (`CREATE TABLE IF NOT EXISTS ... UNIQUE(scan_date, ticker)`), registered via the `_DEFERRED_INITS.append(...)` startup pattern (appended near the end of the file, after the function is defined).
- GET endpoint reads ONLY from the DB table (never triggers a live scan), returns `stale: true` gracefully when empty — never a 500.
- Admin POST endpoint gated by `X-Admin-Token` spawns the scan+alert in a background thread.
- Daily schedule: added to `_OWNER_EMAIL_SCHEDULE` dict + `_TG_KIND_LABEL` + a branch in `_owner_send_now`.
- Alert function dedups via `signal_fire_log` (signal_name + fire_date) so restarts never double-send, and also upserts every match into the dedicated table.
- Frontend: add to the tab `useState<...>` type union, the tab list array, a fetch function + interfaces in `lib/api.ts`, and a new tab-content component in `Dashboard.tsx`.

Verified end-to-end live: admin trigger → scan found 52 matches on real market data → saved to DB (dedup confirmed via signal_fire_log count match) → GET endpoint served them → Telegram alert sent. No changes made to Conviction Stack or Nano Morning Ranking.
