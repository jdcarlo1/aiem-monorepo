---
name: Unusual Puts + Bear Flow wiring notes
description: Key facts learned when wiring the bearish PUT scan and Bear Flow conviction tabs into main.py
---

**_os is NOT a module-level alias in main.py**
Every function that needs `os.getenv()` must do `import os as _os` locally inside the function.
Dozens of existing route functions do this; new routes must follow the same pattern.
**Why:** main.py was built incrementally; `_os` was never made a global alias.

**polygon_rvol_scan column names**
- `price` (not `close_price`)
- `close_strength` (0=closed at day low, 1=closed at day high) — NOT `pct_change`
- Other columns: `rvol`, `open_price`, `high`, `low`, `vwap`, `gap_pct`, `volume`, `avg_volume`
**Why:** Several queries in new code incorrectly assumed close_price/pct_change from the calls tab schema.

**Flask routes after `if __name__ == "__main__":` never register**
Routes must be placed BEFORE `_MODULE_FULLY_LOADED = True` (line ~68663) and definitely before
the `if __name__ == "__main__": _wz_srv_thr.join()` block. The join() blocks forever.
