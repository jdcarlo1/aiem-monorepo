---
name: eod-accumulation weekend hang
description: _after_close logic bug that caused live yfinance scan on weekends, hanging 10+ seconds with no response
---

## The bug
`eod_accumulation()` computed `_after_close` using only time-of-day checks:
- `_h_ea >= 17` (5 PM+)
- `_h_ea < 9` (before 9 AM)
- `_h_ea == 9 and _m_ea < 30` (9:00–9:29)
- `_h_ea == 16 and _m_ea >= 5` (4:05–4:59 PM)

On a **Saturday or Sunday at 10–4 PM ET**, all four conditions were False → `_after_close = False` → triggered a full live yfinance scan → hard hang (0 bytes, 10s+ timeout).

## Fix
Add `not _intraday_scan_allowed()` as the first clause:
```python
_after_close = (
    not _intraday_scan_allowed() or    # weekends, holidays, pre-9:30, post-4:30
    (_h_ea == 16 and _m_ea >= 5) or
    ...
)
```

**Why:** `_intraday_scan_allowed()` is the single authoritative gate for "is the market open right now?" — it covers weekdays-only, holidays, and the 9:30–4:30 window. Any endpoint doing live intraday scans should use it, not hand-roll time-of-day math.

**How to apply:** Any endpoint that has a `_after_close` / `_market_open` check without calling `_intraday_scan_allowed()` is vulnerable to the same weekend hang.
