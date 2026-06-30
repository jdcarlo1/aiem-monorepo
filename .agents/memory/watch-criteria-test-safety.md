---
name: Watch-criteria scan test safety
description: How to safely manually-test _aiem_scan_watch_criteria without polluting production dedupe data or suppressing real alerts
---

`_aiem_scan_watch_criteria()` scans ALL active rows in `aiem_watch_criteria`, not just one you seed for a test, and each match commits immediately (per-row `conn.commit()` inside the loop, not one transaction) into `aiem_watch_alerts`.

**Why:** A manual test that seeds one fake criterion and calls this function will also sweep in every other real, currently-active criterion (e.g. from today's real EOD report) and write real dedupe rows for them tagged with your test's `job_name`. Because `aiem_watch_alerts` is UNIQUE on (criteria_id, ticker, alert_date), those rows silently block the real scheduled job from re-alerting on those same tickers later that day -- a test run can suppress real production alerts with no visible error.

**How to apply:** When manually verifying this function (or anything that calls it): (1) always monkey-patch `_tg_send` first so no real Telegram message goes out, (2) after the test, clean up with `DELETE FROM aiem_watch_alerts WHERE job_name = '<your_test_job_name>'` (matches across ALL criteria_id, not just the one you seeded) before deleting your seeded criteria row, (3) immediately re-query `aiem_watch_criteria` to confirm the real rows are still intact and untouched.
