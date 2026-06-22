---
name: ThreadPoolExecutor shutdown pattern
description: with ThreadPoolExecutor() as ex blocks at exit waiting for ALL futures even after as_completed timeout fires — always use explicit shutdown(wait=False)
---

# Rule
Never use `with ThreadPoolExecutor() as ex:` around a timed loop. The context manager's
`__exit__` calls `shutdown(wait=True)` which blocks until ALL submitted futures complete,
even if `as_completed(timeout=N)` already fired a TimeoutError inside the loop.

**Why:** This was the root cause of bull-flow/top10 hanging for 15+ seconds. The
`as_completed(timeout=7)` loop exited after 7s, but the `with` block exit then waited
for all 150 pending yfinance futures (each potentially 8s) to finish.

**How to apply:** Whenever using a ThreadPoolExecutor with an `as_completed` timeout,
use explicit construction + `shutdown(wait=False, cancel_futures=True)`:

```python
# WRONG — blocks at with-exit even after as_completed times out
with ThreadPoolExecutor(max_workers=N) as ex:
    futures = {ex.submit(fn, t): t for t in tickers}
    for fut in as_completed(futures, timeout=7):
        ...

# CORRECT — abandons pending futures immediately
_ex = ThreadPoolExecutor(max_workers=N)
_futures = {_ex.submit(fn, t): t for t in tickers}
try:
    for fut in as_completed(_futures, timeout=7):
        try:
            r = fut.result(timeout=1)  # short result timeout too
            ...
        except Exception:
            pass
except Exception:
    pass
_ex.shutdown(wait=False, cancel_futures=True)
```

Also: `fut.result(timeout=1)` — keep this short too, because a future yielded by
`as_completed` at t=6.9s (just before the 7s deadline) + `result(timeout=8)` = 14.9s
total, defeating the purpose of the outer timeout.
