---
name: EOD and pre-market job guard bug
description: _intraday_scan_allowed() must never be used for EOD (4:30 PM) or pre-market (8:30 AM) scheduled jobs — it silently blocks them every single day
---

## The Rule
Never wrap EOD or pre-market APScheduler jobs with `if not _intraday_scan_allowed(): return`.

## Why
`_intraday_scan_allowed()` returns True only for `570 <= mins <= 990` (9:30 AM – 4:30 PM ET).

- **4:30 PM EOD jobs** (e.g. OI snapshot): ceiling is exactly 990. Any scheduler jitter firing at 4:31 PM = 991 minutes = guard returns False = job silently skipped. This caused OI snapshots to stop saving after June 18 2026.
- **8:30 AM pre-market jobs** (e.g. pre-market OI refresh): 8:30 AM = 510 minutes, which is below the 570-minute floor. This guard blocked the pre-market OI refresh from EVER running since it was built.

## How to Apply
For EOD and pre-market jobs, replace the intraday guard with a direct market-holiday check:

```python
from datetime import date as _d, datetime as _dt
import pytz as _ptz
_HOLIDAYS = frozenset({_d(2026,1,1), _d(2026,6,19), ...})
if _dt.now(_ptz.timezone("America/New_York")).date() in _HOLIDAYS:
    return
```

The cron trigger (`day_of_week="mon-fri"`) already handles weekends. Only holiday exclusion is needed.

**Safe jobs for `_intraday_scan_allowed()`**: intraday scanners (9:30 AM – 4:00 PM), live Yahoo fetches, gamma/flow scans that need live prices.
