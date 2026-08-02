#!/usr/bin/env python3
"""
verify_inline_copies.py  —  Amendment_A / A3

Verifies that the verbatim function copies embedded in the test files
match the canonical definitions in aiem_options_scheduler.py.

If these diverge, the tests no longer exercise the production code path.

Run from workspace root:
    python3 artifacts/stock-scanner-api/tools/verify_inline_copies.py

Exit 0 = all inline copies match canonical source (PASS)
Exit 1 = at least one divergence found (FAIL)

Normalization applied before comparison:
  • Strip leading indentation (scheduler functions are nested 8 spaces inside
    _execute_job; test-file copies are module-level at 0 spaces).
  • Replace `_math.` with `math.` (scheduler aliases `import math as _math`;
    test files use `math` directly — functionally identical).
  • Collapse trailing blank lines.
  • Ignore differences in module-level constant declarations that exist as
    closure variables in the scheduler (_LIQ_IV_MAX, _MIN_DTE_CHAIN,
    _DELTA_TARGET are module-level constants in test files but local vars
    inside _execute_job in the scheduler).

Functions checked:
  _pick_expiry        lines 1644-1652  (scheduler)
  _liquid_chain       lines 1654-1674  (scheduler)
  _pick_by_delta      lines 1676-1682  (scheduler)
  _bs_d1d2            lines 1595-1602  (scheduler)

Test files checked:
  tests/test_real_chain_selection.py   (_pick_expiry, _liquid_chain, _pick_by_delta)
  tests/test_e2e_pipeline_replay.py    (_bs_d1d2, _pick_expiry, _liquid_chain, _pick_by_delta)
"""

import sys
import re
import pathlib
import datetime
import textwrap

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER = WORKSPACE / "artifacts/stock-scanner-api/aiem_options_scheduler.py"
TEST_UNIT = WORKSPACE / "artifacts/stock-scanner-api/tests/test_real_chain_selection.py"
TEST_E2E  = WORKSPACE / "artifacts/stock-scanner-api/tests/test_e2e_pipeline_replay.py"

_PASS = 0
_FAIL = 0


def _extract_function(lines: list[str], name: str) -> list[str]:
    """
    Extract the body of `def <name>(` from a list of lines.
    Returns the lines of the function (including the def line) stripped of
    their common leading indentation and with trailing blanks removed.
    """
    start = None
    base_indent = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"def {name}("):
            start = i
            base_indent = len(line) - len(stripped)
            break
    if start is None:
        return []

    body = []
    for line in lines[start:]:
        if not line.strip():
            body.append("")
            continue
        indent = len(line) - len(line.lstrip())
        # Stop when we hit a non-blank line at the same or shallower indent
        # (next sibling def or end of block), unless it's the first line.
        if body and indent <= base_indent and line.strip():
            break
        body.append(line)

    # Strip common leading indent
    dedented = []
    for line in body:
        if line.strip():
            dedented.append(line[base_indent:].rstrip())
        else:
            dedented.append("")

    # Remove trailing blank lines
    while dedented and not dedented[-1].strip():
        dedented.pop()

    return dedented


def _normalize(lines: list[str]) -> list[str]:
    """Normalize for comparison: replace _math. → math."""
    return [re.sub(r'\b_math\.', 'math.', ln) for ln in lines]


def _check(label: str, canonical: list[str], copy_: list[str]) -> bool:
    global _PASS, _FAIL
    n_canon = _normalize(canonical)
    n_copy  = _normalize(copy_)
    if n_canon == n_copy:
        print(f"  PASS  {label}")
        _PASS += 1
        return True
    else:
        print(f"  FAIL  {label}")
        # Show first divergence
        for i, (a, b) in enumerate(zip(n_canon, n_copy)):
            if a != b:
                print(f"         first divergence at line {i+1}:")
                print(f"           canonical : {a!r}")
                print(f"           copy      : {b!r}")
                break
        if len(n_canon) != len(n_copy):
            print(f"         line count: canonical={len(n_canon)} copy={len(n_copy)}")
        _FAIL += 1
        return False


def _check_empty(label: str, extracted: list[str], filename: str) -> bool:
    global _FAIL
    if not extracted:
        print(f"  FAIL  {label} — function not found in {filename}")
        _FAIL += 1
        return False
    return True


print("=" * 70)
print("verify_inline_copies.py")
print(f"Run at: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} UTC")
print(f"Scheduler: {SCHEDULER}")
print("=" * 70)

sched_lines  = SCHEDULER.read_text().splitlines()
unit_lines   = TEST_UNIT.read_text().splitlines()
e2e_lines    = TEST_E2E.read_text().splitlines()

# ── Canonical extractions from scheduler ────────────────────────────────────
canon_bs         = _extract_function(sched_lines, "_bs_d1d2")
canon_pick_exp   = _extract_function(sched_lines, "_pick_expiry")
canon_liquid     = _extract_function(sched_lines, "_liquid_chain")
canon_pick_delta = _extract_function(sched_lines, "_pick_by_delta")

for name, canon in [
    ("_bs_d1d2",       canon_bs),
    ("_pick_expiry",   canon_pick_exp),
    ("_liquid_chain",  canon_liquid),
    ("_pick_by_delta", canon_pick_delta),
]:
    if not canon:
        print(f"  FAIL  canonical {name} not found in scheduler — aborting")
        sys.exit(1)

# ── Check test_real_chain_selection.py ───────────────────────────────────────
print("\n--- test_real_chain_selection.py ---")
for name, canon in [
    ("_pick_expiry",   canon_pick_exp),
    ("_liquid_chain",  canon_liquid),
    ("_pick_by_delta", canon_pick_delta),
]:
    copy_ = _extract_function(unit_lines, name)
    label = f"{name}  (test_real_chain_selection.py vs scheduler)"
    if _check_empty(label, copy_, TEST_UNIT.name):
        _check(label, canon, copy_)

# ── Check test_e2e_pipeline_replay.py ────────────────────────────────────────
print("\n--- test_e2e_pipeline_replay.py ---")
for name, canon in [
    ("_bs_d1d2",       canon_bs),
    ("_pick_expiry",   canon_pick_exp),
    ("_liquid_chain",  canon_liquid),
    ("_pick_by_delta", canon_pick_delta),
]:
    copy_ = _extract_function(e2e_lines, name)
    label = f"{name}  (test_e2e_pipeline_replay.py vs scheduler)"
    if _check_empty(label, copy_, TEST_E2E.name):
        _check(label, canon, copy_)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
print("=" * 70)
total = _PASS + _FAIL
verdict = "PASS" if _FAIL == 0 else "FAIL"
print(f"RESULT: {verdict}  ({_PASS} PASS  {_FAIL} FAIL  of {total} checks)")
print(f"Completed: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} UTC")
print("=" * 70)

sys.exit(0 if _FAIL == 0 else 1)
