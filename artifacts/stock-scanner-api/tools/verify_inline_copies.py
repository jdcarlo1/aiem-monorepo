#!/usr/bin/env python3
"""
verify_inline_copies.py  —  Directive_C6 / Amendment_A (A3) + Followup Item 1

Verifies that the verbatim function copies embedded in the test files
match the canonical definitions in aiem_options_scheduler.py.

If these diverge, the tests no longer exercise the production code path.

Run from workspace root:
    python3 artifacts/stock-scanner-api/tools/verify_inline_copies.py

Exit 0 = all checks PASS or STRUCT_DIFF (structural difference noted, no silent skip)
Exit 1 = at least one FAIL or UNCHECKED

Normalization applied before comparison:
  • Strip leading indentation (scheduler functions are nested 8 spaces inside
    _execute_job; test-file copies are module-level at 0 spaces).
  • Replace `_math.` with `math.` (scheduler aliases `import math as _math`;
    test files use `math` directly — functionally identical).
  • Collapse trailing blank lines.

UNCHECKED sentinel (Followup Item 1.2):
  A master FNS registry declares every (canonical_name, copy_name, file) tuple
  that must be verified. If _extract_function() returns empty for either side,
  the check is NOT silently dropped — it is printed as UNCHECKED and counted
  as FAIL. This prevents the silent skip that occurred for _bs_d1d2_FIXED,
  _build_call_data, and _build_put_data in the first run.

STRUCT_DIFF status:
  Used when the canonical source is not a `def` block (e.g., an inline dict
  literal). Extraction is not applicable; the divergence is stated explicitly.
  STRUCT_DIFF is counted separately (not PASS, not FAIL) and printed with
  explanation.  The caller must decide whether to add a field-level comparison.

Functions checked (7 verified + 3 new):
  Scheduler def     → test copy                     file
  ─────────────────────────────────────────────────────────────────────
  _bs_d1d2          → _bs_d1d2          test_e2e_pipeline_replay.py
  _pick_expiry      → _pick_expiry      test_real_chain_selection.py
  _pick_expiry      → _pick_expiry      test_e2e_pipeline_replay.py
  _liquid_chain     → _liquid_chain     test_real_chain_selection.py
  _liquid_chain     → _liquid_chain     test_e2e_pipeline_replay.py
  _pick_by_delta    → _pick_by_delta    test_real_chain_selection.py
  _pick_by_delta    → _pick_by_delta    test_e2e_pipeline_replay.py
  _bs_d1d2          → _bs_d1d2_FIXED   test_failure_reproduction.py  (alias)
  dict literal 1920 → _build_call_data  test_e2e_pipeline_replay.py  (STRUCT_DIFF)
  dict literal 1948 → _build_put_data   test_e2e_pipeline_replay.py  (STRUCT_DIFF)
"""

import sys
import re
import pathlib
import datetime

WORKSPACE    = pathlib.Path(__file__).resolve().parent.parent.parent.parent
SCHEDULER    = WORKSPACE / "artifacts/stock-scanner-api/aiem_options_scheduler.py"
TEST_UNIT    = WORKSPACE / "artifacts/stock-scanner-api/tests/test_real_chain_selection.py"
TEST_E2E     = WORKSPACE / "artifacts/stock-scanner-api/tests/test_e2e_pipeline_replay.py"
TEST_FAILURE = WORKSPACE / "artifacts/stock-scanner-api/tests/test_failure_reproduction.py"

# ── Master registry ──────────────────────────────────────────────────────────
# Each entry: (canonical_name, copy_name, test_file, note)
#   canonical_name = None → canonical is a dict literal (STRUCT_DIFF)
#   copy_name     → function name in test file
FNS = [
    # ── Standard verbatim copies ──────────────────────────────────────────
    ("_bs_d1d2",     "_bs_d1d2",     TEST_E2E,      None),
    ("_pick_expiry", "_pick_expiry", TEST_UNIT,     None),
    ("_pick_expiry", "_pick_expiry", TEST_E2E,      None),
    ("_liquid_chain","_liquid_chain",TEST_UNIT,     None),
    ("_liquid_chain","_liquid_chain",TEST_E2E,      None),
    ("_pick_by_delta","_pick_by_delta",TEST_UNIT,   None),
    ("_pick_by_delta","_pick_by_delta",TEST_E2E,    None),
    # ── Alias: copy has a different name from canonical ────────────────────
    # _bs_d1d2_FIXED is a post-fix label copy of _bs_d1d2; docstring differs.
    ("_bs_d1d2",     "_bs_d1d2_FIXED", TEST_FAILURE,
     "alias: copy name differs from canonical; docstring divergence expected"),
    # ── Structural: canonical is an inline dict literal (not a def) ────────
    # call_data = {**base_fields, ...} at scheduler lines 1920-1947.
    # The test wraps it in a helper function AND omits **base_fields entirely.
    # _extract_function() cannot extract a dict literal → STRUCT_DIFF.
    (None, "_build_call_data", TEST_E2E,
     "canonical is inline dict literal (lines 1920-1947); "
     "test copy omits **base_fields; field-level comparison required"),
    # put_data = {**base_fields, ...} at scheduler lines 1948-1975.
    (None, "_build_put_data",  TEST_E2E,
     "canonical is inline dict literal (lines 1948-1975); "
     "test copy omits **base_fields; field-level comparison required"),
]

# ── Counters ─────────────────────────────────────────────────────────────────
_PASS       = 0
_FAIL       = 0
_STRUCT     = 0
checked     = set()   # (copy_name, test_file.name) — populated on every check

def _extract_function(lines: list, name: str) -> list:
    """
    Extract the body of `def <name>(` from a list of lines.
    Returns dedented lines with trailing blanks removed, or [] if not found.
    """
    start      = None
    base_indent = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"def {name}("):
            start       = i
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
        if body and indent <= base_indent and line.strip():
            break
        body.append(line)

    dedented = []
    for line in body:
        if line.strip():
            dedented.append(line[base_indent:].rstrip())
        else:
            dedented.append("")

    while dedented and not dedented[-1].strip():
        dedented.pop()
    return dedented


def _normalize(lines: list) -> list:
    return [re.sub(r'\b_math\.', 'math.', ln) for ln in lines]


def _check(canonical_name: str, copy_name: str, test_file: pathlib.Path,
           sched_lines: list, test_lines: list, note: str | None) -> None:
    global _PASS, _FAIL

    key = (copy_name, test_file.name)

    # ── Extract canonical from scheduler ─────────────────────────────────
    canon = _extract_function(sched_lines, canonical_name)
    if not canon:
        print(f"  UNCHECKED  {copy_name} ({test_file.name})")
        print(f"             canonical `{canonical_name}` not found as def in scheduler")
        if note:
            print(f"             note: {note}")
        _FAIL += 1
        checked.add(key)
        return

    # ── Extract copy from test file ───────────────────────────────────────
    copy_ = _extract_function(test_lines, copy_name)
    if not copy_:
        print(f"  UNCHECKED  {copy_name} ({test_file.name})")
        print(f"             copy `{copy_name}` not found as def in {test_file.name}")
        if note:
            print(f"             note: {note}")
        _FAIL += 1
        checked.add(key)
        return

    # ── Normalize and compare ─────────────────────────────────────────────
    n_canon = _normalize(canon)
    n_copy  = _normalize(copy_)

    # For aliases: canonical def line has the canonical name; copy def line has
    # the copy name.  Normalise the canonical def line to use the copy name so
    # the comparison tests the body, not the naming artifact.
    is_alias = (canonical_name != copy_name)
    if is_alias and n_canon:
        n_canon[0] = n_canon[0].replace(
            f"def {canonical_name}(", f"def {copy_name}(", 1
        )

    label = f"{copy_name}  ({test_file.name})"
    if is_alias:
        label += "  [alias]"

    if n_canon == n_copy:
        print(f"  PASS   {label}")
        _PASS += 1
    else:
        print(f"  FAIL   {label}")
        for i, (a, b) in enumerate(zip(n_canon, n_copy)):
            if a != b:
                print(f"         first divergence at line {i+1}:")
                print(f"           canonical : {a!r}")
                print(f"           copy      : {b!r}")
                break
        if len(n_canon) != len(n_copy):
            print(f"         line count: canonical={len(n_canon)} copy={len(n_copy)}")
        if note:
            print(f"         note: {note}")
        _FAIL += 1

    checked.add(key)


def _check_struct_diff(copy_name: str, test_file: pathlib.Path,
                       test_lines: list, note: str) -> None:
    """Canonical is not a def — report STRUCT_DIFF unconditionally."""
    global _STRUCT

    key = (copy_name, test_file.name)

    copy_ = _extract_function(test_lines, copy_name)
    present = "present" if copy_ else "ABSENT"

    print(f"  STRUCT_DIFF  {copy_name}  ({test_file.name})")
    print(f"               copy in test file: {present}")
    print(f"               {note}")
    _STRUCT += 1
    checked.add(key)


# ── Load files ───────────────────────────────────────────────────────────────
print("=" * 72)
print("verify_inline_copies.py")
print(f"Run at  : {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} UTC")
print(f"Scheduler: {SCHEDULER.name}")
print("=" * 72)

sched_lines   = SCHEDULER.read_text().splitlines()
unit_lines    = TEST_UNIT.read_text().splitlines()
e2e_lines     = TEST_E2E.read_text().splitlines()
failure_lines = TEST_FAILURE.read_text().splitlines()

file_map = {
    TEST_UNIT:    unit_lines,
    TEST_E2E:     e2e_lines,
    TEST_FAILURE: failure_lines,
}

# ── Run all entries from master registry ─────────────────────────────────────
current_file = None
for (canonical_name, copy_name, test_file, note) in FNS:
    if test_file != current_file:
        print(f"\n--- {test_file.name} ---")
        current_file = test_file

    test_lines = file_map[test_file]

    if canonical_name is None:
        _check_struct_diff(copy_name, test_file, test_lines, note)
    else:
        _check(canonical_name, copy_name, test_file, sched_lines, test_lines, note)

# ── UNCHECKED sentinel (Followup Item 1.2) ───────────────────────────────────
# Any name in FNS not reached via _check/_check_struct_diff → UNCHECKED FAIL.
# This should never fire with the explicit loop above, but guards against
# future FNS additions whose check branch is accidentally skipped.
print("\n--- UNCHECKED sentinel ---")
sentinel_fired = False
for (canonical_name, copy_name, test_file, note) in FNS:
    key = (copy_name, test_file.name)
    if key not in checked:
        print(f"  UNCHECKED  {copy_name}  ({test_file.name})  — fell through registry loop")
        _FAIL += 1
        sentinel_fired = True
if not sentinel_fired:
    print("  OK — all registry entries were reached")

# ── Summary ──────────────────────────────────────────────────────────────────
print()
print("=" * 72)
total   = _PASS + _FAIL
verdict = "PASS" if _FAIL == 0 else "FAIL"
print(f"RESULT   : {verdict}  ({_PASS} PASS  {_FAIL} FAIL  {_STRUCT} STRUCT_DIFF  "
      f"of {total + _STRUCT} entries)")
print(f"Completed: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} UTC")
print("=" * 72)

sys.exit(0 if _FAIL == 0 else 1)
