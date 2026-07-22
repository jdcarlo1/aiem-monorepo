---
name: Options pipeline CronTrigger UTC bug
description: BackgroundScheduler(timezone=ET) does NOT propagate to CronTrigger(); requires explicit timezone kwarg per trigger. FIXED 2026-07-22 Directive 21 for all aiem_process.py jobs.
---

**Rule:** Every CronTrigger() call in aiem_process.py must include `timezone=ET` explicitly.

**Why:** APScheduler BackgroundScheduler(timezone=ET) sets the scheduler's default display/reporting timezone but does NOT propagate to individual CronTrigger instances. CronTrigger without an explicit timezone defaults to UTC. Confirmed from production log: `scheduled at 2026-07-22 14:00:00+00:00` (UTC suffix) for a trigger intended to fire at 11:00 AM ET.

**How to apply:** When adding any new CronTrigger() call to aiem_process.py or aiem_options_scheduler.py, always include `timezone=ET` (or `timezone=_ET`) as a keyword argument.

**Fixed:** 2026-07-22, Directive 21. All 12 non-compliant triggers in aiem_process.py now have timezone=ET. One intentional exception: `CronTrigger(hour=3, minute=2)` nightly reset — explicitly excluded from fix scope.

**aiem_telegram_notifier.py:** Already had timezone=ET on all CronTrigger calls before this fix. Contrast example in logs: `scheduled at 2026-07-22 10:00:00-04:00` (EDT correct).
