---
name: aiem_options_phase2 sys.path rule
description: capture_trade_record() rho/charm/vanna import fails silently because the scheduler's CWD is /home/runner/workspace, not stock-scanner-api/
---

## Rule
`aiem_options_phase2.py:capture_trade_record()` must add its own directory to `sys.path` before
`from aiem_strat_engine.greeks import bs_rho, bs_charm, bs_vanna`. The scheduler workflow runs as
`python3 /home/.../aiem_options_scheduler.py` from workspace root, so `aiem_strat_engine/` is not
on the path.

## Fix in place (2026-07-28)
```python
import sys as _sys, os as _os
_p2_dir = _os.path.dirname(_os.path.abspath(__file__))
if _p2_dir not in _sys.path:
    _sys.path.insert(0, _p2_dir)
from aiem_strat_engine.greeks import bs_rho, bs_charm, bs_vanna
```

**Why:** The try/except around the compute block swallows the `ModuleNotFoundError` silently,
setting rho=charm=vanna=None. No log warning appears in normal operation. This manifested
as 0 rho/charm/vanna values in all 28 oe_trade_records rows prior to the fix.

**How to apply:** Any new module in `artifacts/stock-scanner-api/` that imports `aiem_strat_engine`
or other sibling packages from within a lazy runtime import should use `__file__`-relative path injection.
