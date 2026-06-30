---
name: DB-backed chart images on alert kinds
description: Shared telegram_charts.py module attaches stock chart PNGs to Telegram/email alert kinds across two independent processes; key gotchas when extending it.
---

Shared module: `artifacts/stock-scanner-api/telegram_charts.py`. Used by both
`aiem_autonomous.py` (root, 24/7 BlockingScheduler) and
`artifacts/stock-scanner-api/main.py` (Flask web process) via the same import.

**Design (do not deviate without re-checking with the user):**
- Chart data comes ONLY from `polygon_market_daily` (DB), never a live
  yfinance/Polygon/Tradier call — avoids re-triggering the 9:30-9:45 AM
  market-open burst-saturation bug.
- One `sendPhoto` per alert (multi-panel grid, max 6 tickers), never one photo
  per ticker or a media-group album — respects Telegram rate limits.
- Every function in the module swallows its own exceptions and returns a
  falsy value; it must never break the caller's existing text-alert flow.
- In `main.py`, the wiring pattern is a single helper `_send_owner_chart(kind,
  title, tickers)` called right after the ticker list is finalized in each
  sender function, before the email/Telegram send.

**Why:** the user explicitly scoped this to "DB-backed, zero live API calls"
across ~17 alert kinds; any new alert kind added later should follow the same
`_send_owner_chart(...)` call pattern, sourcing the ticker list from
already-fetched in-memory data (dict key or tuple index — verify against the
actual SQL column order / dict shape before wiring, don't assume).

**Known gotchas:**
- matplotlib's default font (DejaVu Sans) has no emoji glyphs — passing an
  emoji-prefixed title straight to `fig.suptitle()` renders a missing-glyph
  box in the PNG. Fix: strip to ASCII only for the matplotlib title; the
  Telegram caption (separate field, full UTF-8) keeps the emoji.
- `main.py` in this project is ~47K lines. The `read` tool has been observed
  serving a STALE cached snapshot of this specific file (reporting far fewer
  lines than `wc -l` shows) after large edit sessions. Always cross-check
  with `wc -l` and prefer `grep -n -A` for line-numbered context on this file
  if `read` line numbers look suspiciously small/wrong.
- Some `_TG_KIND_LABEL` dict entries have no actual sender branch in
  `_owner_send_now` (e.g. `unusual_calls`) — a pre-existing latent gap
  unrelated to chart wiring; don't assume every label key has a callable
  sender.
- Functions whose payload is findings/hypotheses prose rather than a clean
  ticker list (e.g. `aiem_digest`, `aiem_nightly_learn`,
  `aiem_missed_morning_check`) are intentionally NOT chart-wired.
