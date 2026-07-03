---
name: simulation_lock structural check
description: is_live_trading_enabled() requires 3 env vars; structural gate must use assert_simulation_mode() instead
---

`is_live_trading_enabled()` in `simulation_lock.py` returns False unless ALL THREE of these are set:
- `LIVE_TRADING_ENABLED=true`
- `LIVE_TRADING_EXPECTED_PHRASE=<phrase>`
- `LIVE_TRADING_CONFIRMATION_PHRASE=<same phrase>`

Setting only `LIVE_TRADING_ENABLED=true` returns False. This makes it unsuitable as a structural gate check.

**Correct structural pattern:**
```python
try:
    from simulation_lock import assert_simulation_mode
    assert_simulation_mode("caller_name")
except Exception as _exc:
    hard_blockers.append(f"simulation lock blocked: ... [{type(_exc).__name__}] {_exc}")
```

`assert_simulation_mode()` raises `LiveTradingBlockedError` when live trading IS enabled (all three vars match). Normal paper-mode calls return without raising.

**Why:** The two-factor design is intentional — a typo or partial config should NOT accidentally enable live trading. The structural gate must raise on the dangerous condition, not check a flag that could silently be incomplete.
