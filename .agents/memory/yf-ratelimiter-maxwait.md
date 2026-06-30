---
name: Yahoo rate limiter max_wait
description: How to prevent HTTP request threads from hanging indefinitely when the yfinance rate limiter is saturated by morning scheduler burst
---

## The rule
`_YFRateLimiter.acquire()` must accept `max_wait=None`. The curl_cffi patch (`_cffi_patched_request`) must call `acquire(max_wait=3.0)` so HTTP request threads bail in ≤3s instead of waiting forever.

## Why
During 9:30-9:45 market-open burst, 4 scheduler threads saturate the 3-token/sec bucket. HTTP request threads (user tab loads) queue behind them in `acquire()` indefinitely → tabs spin forever. With `max_wait=3.0`, they raise `RuntimeError` → cffi raises `_CffiErr` → endpoint exception handler returns cache/empty in <3s.

**Key distinction:**
- Scheduler scan threads: use `_YF_RATE_LIMITER.acquire()` directly (no max_wait = unlimited wait). Scans keep running.
- HTTP request threads: go through `_cffi_patched_request` which uses `max_wait=3.0`.

## Implementation
```python
def acquire(self, max_wait=None):
    _deadline = (_time_cb.monotonic() + max_wait) if max_wait is not None else None
    while True:
        with self._lock:
            ...token bucket logic...
        if _deadline is not None and _time_cb.monotonic() >= _deadline:
            raise RuntimeError(f"Yahoo rate limiter busy — backed off after {max_wait:.1f}s")
        _time_cb.sleep(0.05)
```

In `_cffi_patched_request`:
```python
try:
    _YF_RATE_LIMITER.acquire(max_wait=3.0)
except RuntimeError:
    raise _CffiErr("Yahoo rate limiter saturated — request backed off")
```

## Interval job guards (9:30-9:44 burst window)
Three interval jobs lacked morning time guards — add to any interval job that fires in the 9:30-9:59 window:
- `_run_vwap_reclaim_scan` (every 5 min)
- `_run_call_sweep_scan` (every 15 min)
- `_run_exit_alert_scan` (every 15 min)

Guard pattern:
```python
import datetime as _dt_xx
_now_xx = _dt_xx.datetime.now(_ET)
if _now_xx.hour == 9 and _now_xx.minute < 45:
    return  # hold off during 9:30-9:44 market-open burst window
```
