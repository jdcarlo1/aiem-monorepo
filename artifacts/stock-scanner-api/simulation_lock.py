"""
simulation_lock.py
---------------------
THE most important file in this entire package if you ever plan to let an
agent make autonomous decisions, even in paper mode.

This is not a setting the agent can reason its way around. Every single
order-placement function in your system should import `assert_simulation_mode()`
and call it as the FIRST line of the function body — before any other logic
runs. If the check fails, execution stops immediately with an exception.

Design principles:
  1. The lock checks an environment variable, NOT a database value or a
     config file the agent might have write access to. Env vars set at
     the process/container level are the hardest thing for a misbehaving
     agent to flip on its own.
  2. The check requires the EXACT string "true" (case-sensitive) to allow
     live trading — anything else (unset, "True", "1", typo) fails safe
     into simulation mode. Fail-safe defaults matter more than convenience.
  3. A second, independent check (LIVE_TRADING_CONFIRMATION_PHRASE) must
     ALSO match a phrase you set yourself — meaning enabling live trading
     requires two separate deliberate actions, not one flipped flag.
  4. Every call to assert_simulation_mode() is logged, so you have a
     complete audit trail of every single decision point that checked in.

To ever go live (months from now, if ever), you would need to:
  export LIVE_TRADING_ENABLED=true
  export LIVE_TRADING_CONFIRMATION_PHRASE="<whatever phrase you choose>"
in the production environment ONLY — never in the paper-trading agent's
environment, which should not even have these variables defined.
"""

import os
import sys
import datetime as dt
import json
from typing import Optional


class LiveTradingBlockedError(Exception):
    """Raised whenever code attempts a real trade while not in confirmed
    live mode. This should always be allowed to propagate and crash the
    calling function — never caught and silently ignored anywhere."""
    pass


_LOG_PATH = os.environ.get("SIMULATION_LOCK_LOG_PATH", "simulation_lock_audit.jsonl")


def _audit_log(event: str, details: dict):
    entry = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "event": event,
        "details": details,
    }
    try:
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        print(f"[simulation_lock] WARNING: could not write audit log: {entry}", file=sys.stderr)


def is_live_trading_enabled() -> bool:
    """Returns True only if BOTH the enable flag and the confirmation
    phrase are correctly set. Returns False for absolutely any other case,
    including missing variables, typos, or partial configuration."""
    enabled = os.environ.get("LIVE_TRADING_ENABLED", "false")
    confirmation = os.environ.get("LIVE_TRADING_CONFIRMATION_PHRASE", "")
    expected_confirmation = os.environ.get("LIVE_TRADING_EXPECTED_PHRASE", "")

    if enabled != "true":
        return False
    if not expected_confirmation:
        return False
    return confirmation == expected_confirmation


def assert_simulation_mode(caller_name: str = "unknown"):
    """Call this as the FIRST line of any function that could place a real
    order, move real money, or otherwise have real-world financial effect.

    Raises LiveTradingBlockedError if somehow live trading appears enabled
    AND you haven't explicitly intended that (this function is named
    'assert_simulation_mode' — if you genuinely want to go live one day,
    you'd remove or bypass this call deliberately in that specific
    function, not change a flag that silently affects everything).
    """
    live = is_live_trading_enabled()
    _audit_log("simulation_check", {"caller": caller_name, "live_trading_enabled": live})

    if live:
        raise LiveTradingBlockedError(
            f"[{caller_name}] Live trading flags are set, but this function "
            f"calls assert_simulation_mode() and will ALWAYS refuse to execute "
            f"a real trade. Remove this call explicitly in code review if you "
            f"truly intend this specific function to trade live."
        )


def require_paper_mode_decorator(func):
    """Decorator version — wrap any order/trade function with this and it
    will refuse to execute if live trading flags are somehow set, with zero
    chance of the wrapped function's own logic accidentally skipping the check."""
    def wrapper(*args, **kwargs):
        assert_simulation_mode(caller_name=func.__name__)
        return func(*args, **kwargs)
    return wrapper


def get_audit_trail(limit: int = 100) -> list:
    """Read back the simulation-lock audit log for review."""
    if not os.path.exists(_LOG_PATH):
        return []
    with open(_LOG_PATH) as f:
        lines = f.readlines()[-limit:]
    return [json.loads(l) for l in lines]


EXAMPLE_USAGE = '''
from simulation_lock import assert_simulation_mode, require_paper_mode_decorator

# Pattern 1: explicit call as first line
def place_paper_order(ticker, qty, side):
    assert_simulation_mode(caller_name="place_paper_order")
    # ... paper order logic only, no real broker API call ...

# Pattern 2: decorator (preferred — impossible to forget)
@require_paper_mode_decorator
def place_paper_order(ticker, qty, side):
    # ... paper order logic only ...
'''


if __name__ == "__main__":
    print("Live trading enabled:", is_live_trading_enabled())
    print("\nExample usage:")
    print(EXAMPLE_USAGE)
