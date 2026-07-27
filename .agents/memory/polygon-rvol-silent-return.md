---
name: Polygon RVOL silent-return fix
description: Three silent-return sites in _polygon_full_market_scan / _polygon_grouped_daily that swallowed all scan failures with zero log output or alerts.
---

## The rule
Any `if not <data>: return []` inside `_polygon_full_market_scan` or `_polygon_grouped_daily` must log an error AND fire a Telegram alert before returning.  A bare silent return means the scan fails invisibly, `polygon_rvol_scan` is not updated, and the paper trade pipeline silently produces NO_CANDIDATES with no owner notification.

## Why
On 2026-07-27 the production server was offline at 8:35 AM ET (premarket-uptime gap), so the scan missed entirely.  Separately the code had a two-step silent failure chain: `_polygon_grouped_daily` returns `{}` silently when `POLYGON_API_KEY` is unset → `daily_data` stays empty → `_polygon_full_market_scan` returns `[]` silently.  This chain would hide API key rotation failures, Polygon 403s, and holiday ranges with no log output whatsoever.

## How to apply
- All three sites are patched (confirmed sha256 before=`9e2064ab…` after=`dbf0b0c9…`).
- If adding new early-exit paths in polygon scan functions, always: `app.logger.error(...)` + `_tg_send(...)` before the `return []`.
- `_polygon_recent_trading_days(5)` on Monday returns `days[0] = most recent Friday` (not yesterday = Sunday-1 = Saturday).  The function skips weekends.  `max(scan_date)` being Friday on a Monday is **correct**, not stale.

## Full site inventory (as of 2026-07-27)
| Line | Function | Action |
|------|----------|--------|
| L4721 | `_polygon_fetch_calls` | `if not _key: return None` — low severity, caller handles; left as-is |
| L64859 | `_polygon_grouped_daily` | `if not _key: return {}` — **fixed**: `app.logger.error()` added |
| L64893 | `_polygon_full_market_scan` | `if not days: return []` — **fixed**: error + Telegram |
| L64909 | `_polygon_full_market_scan` | `if not daily_data: return []` — **fixed**: error + Telegram (the critical path) |
