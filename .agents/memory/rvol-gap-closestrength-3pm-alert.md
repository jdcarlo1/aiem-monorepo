---
name: RVOL/Gap/CloseStrength 3PM combo alert
description: Design constraints behind the daily 3PM Telegram alert for the backtested RVOL>2.5+Gap>0.5%+CloseStrength>0.6 combo, and what polygon_market_daily's gap_pct actually measures.
---

The combo (rvol>2.5, gap_pct>0.5, close_strength>0.6 — backtested up to 87.65% WR) can only be evaluated on a **fully completed** trading session: close_strength and RVOL both need the day's final high/low/close/volume, which aren't final until the 4:00 PM close, and Polygon's grouped-daily endpoint 403s for "today" until the session closes (see polygon-403-today-snapshot.md) — there is no full-market live/intraday snapshot on the current plan.

**Why this matters:** a "3PM alert" cannot compute this combo for *today* in real time across the full market. The implemented alert (send_rvol_combo_alert in aiem_telegram_notifier.py, scheduled 15:00 ET Mon-Fri) instead reports the most recently COMPLETED session's hits from polygon_market_daily, each paired with a live Tradier quote so the owner sees real-time follow-through since that close. This is a deliberate, disclosed design choice, not a workaround to hide — don't "fix" it into a same-day intraday scan without re-deriving a full-market live data source first.

**How to apply:** if asked to make this (or any similar EOD-metric-based combo) fire earlier or scan live, first check whether the metric genuinely needs the session's final bar. If yes, the earliest a same-day full-market pass can exist is after Polygon's grouped-daily for that date becomes available (historically: usable by the following morning's 8:35 AM job, per the existing `_polygon_full_market_scan` cadence — same-day evening availability has not been verified).

**gap_pct quirk:** in the polygon_market_daily backfill/ingest path, `gap_pct = (close - prev_close) / prev_close * 100` — i.e. a full-day close-to-close % move, NOT an opening gap (open vs prev_close). A separate live intraday scanner elsewhere in main.py (~L54424) computes a true open-vs-prev_close gap for a different purpose. Don't assume "gap_pct" means the same thing across every table/module — check the specific formula before trusting a cross-module comparison.

Query filters applied for the alert to avoid noise: close_price >= $1.00, ticker has no "." (excludes warrants/rights like `FCRS.WS`). This still lets a few leveraged ETFs/ETNs through (e.g. MQQQ, DIG, WTIU, NRGU) since their intraday range also produces a valid close_strength — they are not filtered out because the original backtest didn't exclude them either.
