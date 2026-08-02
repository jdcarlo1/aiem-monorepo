#!/usr/bin/env python3
"""
ITEM 9 — FAILURE REPRODUCTION
Directive_RealChain_RevC

This file:
  1. Reproduces the original crash using the PRE-FIX _bs_d1d2 (verbatim from
     commit 55b9364, lines 1595-1602).
  2. Shows the fix no longer crashes (POST-FIX version, verbatim from HEAD).

The original production failure was:
  job_id=204, ticker=HAL, scan_date=2026-07-31
  trace_id=b2e629c518368450, timestamp=2026-08-02T05:13Z UTC
  Error: TypeError: '<=' not supported between instances of 'NoneType' and 'int'
  Location: aiem_options_scheduler.py line 1597, _bs_d1d2, `K <= 0`
  Root cause: Saturday market closure → all Polygon bid/ask=0 → _liquid_chain
              rejects all 230 contracts → call_strike=None → _bs_d1d2 called
              with K=None → comparison `None <= 0` raises TypeError.

Run:  python3 artifacts/stock-scanner-api/tests/test_failure_reproduction.py
"""

import sys
import math
import traceback as _tb
import datetime

print("=" * 70)
print("ITEM 9 — FAILURE REPRODUCTION")
print(f"Run at: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} UTC")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION A: PRE-FIX CODE (verbatim from commit 55b9364, lines 1595-1602)
# ─────────────────────────────────────────────────────────────────────────────
print("\n--- SECTION A: PRE-FIX _bs_d1d2 (commit 55b9364, lines 1595-1602) ---")
print("Source code:")
print("  def _bs_d1d2(S, K, sig, T):")
print('      """d1, d2 from Black-Scholes (r=0 simplification)."""')
print("      if sig <= 0 or T <= 0 or S <= 0 or K <= 0:")
print("          return 0.0, -0.1")
print("      d1 = (math.log(S / K) + 0.5 * sig**2 * T) / (sig * math.sqrt(T))")
print("      return d1, d1 - sig * math.sqrt(T)")

def _bs_d1d2_BUGGY(S, K, sig, T):
    """PRE-FIX: verbatim from commit 55b9364.
    Guard is: `if sig <= 0 or T <= 0 or S <= 0 or K <= 0:`
    When K=None, `None <= 0` raises TypeError."""
    if sig <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0, -0.1
    d1 = (math.log(S / K) + 0.5 * sig**2 * T) / (sig * math.sqrt(T))
    return d1, d1 - sig * math.sqrt(T)

# Reproduce the exact production crash scenario:
# call_strike=None (because all Polygon contracts had bid=0 on Saturday)
spot      = 32.25   # POLY_CLOSE_PRICE from oe_indicator_snapshots job_id=204
front_iv  = 0.5414  # OSS_FRONT_IV from oe_indicator_snapshots job_id=204
_T        = 9 / 252.0
call_strike_SATURDAY = None  # all 230 contracts rejected by _liquid_chain

print(f"\nReproducing crash scenario:")
print(f"  spot={spot}, call_strike={call_strike_SATURDAY}, front_iv={front_iv}, _T={_T:.6f}")
print(f"  Call: _bs_d1d2_BUGGY({spot}, {call_strike_SATURDAY}, {front_iv}, {_T:.6f})")

crash_raised = False
crash_type   = None
crash_msg    = None
crash_trace  = None

try:
    result = _bs_d1d2_BUGGY(spot, call_strike_SATURDAY, front_iv, _T)
    print(f"  [UNEXPECTED] Did NOT crash — returned {result}")
except TypeError as e:
    crash_raised = True
    crash_type   = "TypeError"
    crash_msg    = str(e)
    crash_trace  = _tb.format_exc()
    print(f"  [CONFIRMED CRASH]")
    print(f"  Exception type: {crash_type}")
    print(f"  Exception message: {crash_msg}")
    print("  Stack trace:")
    for line in crash_trace.strip().split("\n"):
        print(f"    {line}")
except Exception as e:
    crash_raised = True
    crash_type   = type(e).__name__
    crash_msg    = str(e)
    print(f"  [CRASH (unexpected type {crash_type})]: {crash_msg}")

_PASS_count = 0
_FAIL_count = 0

def _assert(label, condition, got="", expected=""):
    global _PASS_count, _FAIL_count
    if condition:
        print(f"  PASS  {label}")
        _PASS_count += 1
    else:
        print(f"  FAIL  {label}  expected={expected!r}  got={got!r}")
        _FAIL_count += 1

print()
_assert("PRE-FIX: K=None raises TypeError",
        crash_raised and crash_type == "TypeError",
        got=f"raised={crash_raised} type={crash_type}")
_assert("PRE-FIX: error message matches NoneType comparison",
        crash_msg is not None and "NoneType" in (crash_msg or ""),
        got=crash_msg)

# Additional pre-fix crash scenarios
for label, args in [
    ("PRE-FIX: S=None raises TypeError", (None, 33.0, 0.54, _T)),
    ("PRE-FIX: sig=None raises TypeError", (32.5, 33.0, None, _T)),
    ("PRE-FIX: T=None raises TypeError",  (32.5, 33.0, 0.54, None)),
]:
    try:
        _bs_d1d2_BUGGY(*args)
        _assert(label, False, got="no exception raised")
    except TypeError:
        _assert(label, True)
    except Exception as e:
        _assert(label, False, got=f"{type(e).__name__}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION B: POST-FIX CODE (verbatim from HEAD, lines 1595-1602)
# ─────────────────────────────────────────────────────────────────────────────
print("\n--- SECTION B: POST-FIX _bs_d1d2 (HEAD commit 82861df, lines 1595-1602) ---")
print("Source code:")
print("  def _bs_d1d2(S, K, sig, T):")
print('      """d1, d2 from Black-Scholes (r=0 simplification).')
print("      C6/FIX-3: all four args guarded for None before any comparison.\"\"\"")
print("      if (K is None or S is None or sig is None or T is None")
print("              or sig <= 0 or T <= 0 or S <= 0 or K <= 0):")
print("          return 0.0, -0.1")
print("      d1 = (math.log(S / K) + 0.5 * sig**2 * T) / (sig * math.sqrt(T))")
print("      return d1, d1 - sig * math.sqrt(T)")

def _bs_d1d2_FIXED(S, K, sig, T):
    """d1, d2 from Black-Scholes (r=0 simplification).
    C6/FIX-3: all four args guarded for None before any comparison."""
    if (K is None or S is None or sig is None or T is None
            or sig <= 0 or T <= 0 or S <= 0 or K <= 0):
        return 0.0, -0.1
    d1 = (math.log(S / K) + 0.5 * sig**2 * T) / (sig * math.sqrt(T))
    return d1, d1 - sig * math.sqrt(T)

print(f"\nSame scenario with POST-FIX:")
print(f"  _bs_d1d2_FIXED({spot}, {call_strike_SATURDAY}, {front_iv}, {_T:.6f})")

try:
    result_fixed = _bs_d1d2_FIXED(spot, call_strike_SATURDAY, front_iv, _T)
    _assert("POST-FIX: K=None does NOT crash",     True)
    _assert("POST-FIX: returns sentinel (0.0, -0.1)",
            result_fixed == (0.0, -0.1), got=result_fixed, expected=(0.0, -0.1))
    print(f"  Result: {result_fixed}")
except Exception as e:
    _assert("POST-FIX: K=None does NOT crash", False, got=f"{type(e).__name__}: {e}")

for label, args, expect in [
    ("POST-FIX: S=None → sentinel",    (None, 33.0, 0.54, _T),    (0.0, -0.1)),
    ("POST-FIX: sig=None → sentinel",  (32.5, 33.0, None, _T),    (0.0, -0.1)),
    ("POST-FIX: T=None → sentinel",    (32.5, 33.0, 0.54, None),  (0.0, -0.1)),
    ("POST-FIX: all valid → d1/d2",    (32.5, 33.0, 0.54, _T),    "VALID"),
]:
    try:
        r = _bs_d1d2_FIXED(*args)
        if expect == "VALID":
            _assert(label, math.isfinite(r[0]) and math.isfinite(r[1]) and r[0] != r[1],
                    got=r)
        else:
            _assert(label, r == expect, got=r, expected=expect)
    except Exception as e:
        _assert(label, False, got=f"{type(e).__name__}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION C: FIX-2 — Early exit (not in pre-fix; verifies the full crash path)
# ─────────────────────────────────────────────────────────────────────────────
print("\n--- SECTION C: FIX-2 early exit (new in HEAD — not in commit 55b9364) ---")
print("Pre-fix behaviour: when call_strike=None AND put_strike=None,")
print("the pipeline continued into _bs_d1d2(spot, None, ...) → TypeError.")
print("Post-fix: explicit ValueError raised BEFORE _bs_d1d2 is called.")

# Pre-fix: no guard — pipeline falls through to _bs_d1d2 with None K
def _pipeline_no_exit_guard(call_strike, put_strike, spot, front_iv, _T):
    """Simulates pre-fix pipeline: no early exit, calls _bs_d1d2 directly."""
    # Would call: _bs_d1d2_BUGGY(spot, call_strike, front_iv, _T)
    _bs_d1d2_BUGGY(spot, call_strike, front_iv, _T)

def _pipeline_with_exit_guard(call_strike, put_strike, spot, front_iv, _T):
    """Simulates post-fix pipeline: FIX-2 check before _bs_d1d2."""
    if call_strike is None and put_strike is None:
        raise ValueError(
            "not ready_for_decision: NO_LIQUID_CONTRACTS — "
            f"expiry=2026-07-31; ticker=HAL"
        )
    _bs_d1d2_FIXED(spot, call_strike, front_iv, _T)

# Pre-fix: crashes with TypeError
try:
    _pipeline_no_exit_guard(None, None, spot, front_iv, _T)
    _assert("PRE-FIX pipeline (no FIX-2): crashes with TypeError",
            False, got="no exception raised")
except TypeError as e:
    _assert("PRE-FIX pipeline (no FIX-2): crashes with TypeError", True)
    print(f"  Confirmed TypeError: {e}")
except Exception as e:
    _assert("PRE-FIX pipeline (no FIX-2): crashes with TypeError",
            False, got=f"{type(e).__name__}: {e}")

# Post-fix: raises clean ValueError (routes to NO_TRADE_GATES, not FAILED)
try:
    _pipeline_with_exit_guard(None, None, spot, front_iv, _T)
    _assert("POST-FIX pipeline (FIX-2): raises clean ValueError",
            False, got="no exception raised")
except ValueError as e:
    _assert("POST-FIX pipeline (FIX-2): raises clean ValueError", True)
    _assert("POST-FIX pipeline (FIX-2): prefix 'not ready_for_decision'",
            str(e).startswith("not ready_for_decision"), got=str(e)[:60])
    print(f"  Clean ValueError: {e}")
except Exception as e:
    _assert("POST-FIX pipeline (FIX-2): raises clean ValueError",
            False, got=f"{type(e).__name__}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
total = _PASS_count + _FAIL_count
print(f"TOTAL: {_PASS_count} PASS  {_FAIL_count} FAIL  ({total} assertions)")
print(f"Completed: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} UTC")
print("=" * 70)

sys.exit(0 if _FAIL_count == 0 else 1)
