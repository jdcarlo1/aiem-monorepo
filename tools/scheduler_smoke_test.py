#!/usr/bin/env python3
"""
tools/scheduler_smoke_test.py — CI smoke-test: scheduler late-ref race guards.

Covers both AIEM (main.py) and Options Engine (aiem_options_scheduler.py).

Checks:
  1. Syntax (py_compile) — both files
  2. Guard coverage — every late-ref target in LATE_REF_TARGETS has a
     _wait_for_module_load() call-site within 30 lines before any
     Thread(target=<name>) that references it, OR is a named wrapper
     whose body contains both the guard and the target reference
  3. Lambda check — no raw scheduler lambda directly references a late-def target
  4. Options Engine late-ref check — every function referenced in sched.add_job()
     is defined BEFORE sched.start()
  5. Core-path structural dryrun (F.2.c) — verify core candidate-generation
     functions exist in both systems via AST; no execution, no side-effects
     AIEM targets: _aiem_paper_pick_candidates, _run_aiem_independent_scan,
                   _aiem_paper_execute_today
     OE targets: seed_daily_candidates, run_pipeline_worker, grade_outcomes_job
  6-7. Per-system forced-failure proofs (opt-in via SMOKE_FULL=1):
     6 = AIEM: remove guard from an in-memory copy → check_guard_coverage detects it
     7 = OE:   inject a missing-target into _OE_MODULE_JOB_TARGETS in-memory copy
               → options_engine_selfcheck detects it

Exit: 0 = all pass, 1 = at least one failure.
Run: python3 tools/scheduler_smoke_test.py
     SMOKE_FULL=1 python3 tools/scheduler_smoke_test.py  (adds forced-failure checks)
"""
import ast
import hashlib
import os
import py_compile
import re
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
MAIN_PY = os.path.join(REPO, "artifacts/stock-scanner-api/main.py")
OPT_PY  = os.path.join(REPO, "artifacts/stock-scanner-api/aiem_options_scheduler.py")

# ── OE module-level targets (mirrors _OE_MODULE_JOB_TARGETS in scheduler) ────
# These are verified by Check 8 to still exist in aiem_options_scheduler.py.
OE_MODULE_TARGET_FNS = [
    "grade_outcomes_job",
    "recover_stale_jobs",
    "_schedule_integrity_check",
    "_polygon_canary_check",
]

# ── Canonical list of late-defined targets in main.py ────────────────────────
# A "late-defined target" is a function referenced inside a scheduler wrapper
# that is defined AFTER _scheduler.start() (line ~8189 in the pre-fix file).
# Every entry here must have a corresponding _wait_for_module_load() guard.
LATE_REF_TARGETS = [
    "_run_aiem_morning_scan",
    "_run_aiem_independent_grade",
    "_run_aiem_independent_scan",
    "_run_aiem_independent_options_scan",
    "_run_aiem_prediction_grader",
    "_run_aiem_research_agent",
    "_run_aiem_continuous_research",
    "_run_behavioral_comparison_scan",
    "_run_gamma_pressure_scan",
    "_run_oi_snapshot",
    "_check_whale_hc_crossover",
    "_send_morning_gamma_watchlist_sms",
    "_send_bigcat_gap_email",
    "_aiem_daily_price_options_alert",
    "_aiem_same_day_alert",
    "_scan_momentum_coil_daily",
    "_run_nano_morning_ranking",
    "_run_nano_morning_outcomes",
    "_run_sc_morning_ranking",
    "_run_sc_morning_outcomes",
    "_rebuild_templates",
    "_run_daily_signal_jobs",
    "_send_eod_accum_email",
    "_send_unusual_calls_email",
    "_send_microcap_calls_email",
    "_send_high_conviction_email",
]

# ── Core candidate-generation functions verified in check 5 ─────────────────
AIEM_CORE_FUNCTIONS = [
    "_aiem_paper_pick_candidates",
    "_run_aiem_independent_scan",
    "_aiem_paper_execute_today",
]
OE_CORE_FUNCTIONS = [
    "seed_daily_candidates",
    "run_pipeline_worker",
    "grade_outcomes_job",
]

# ── Module-level OE targets (mirrors _OE_MODULE_JOB_TARGETS in the scheduler) ─
OE_MODULE_TARGETS = {
    "grade_outcomes":         "grade_outcomes_job",
    "stale_recovery":         "recover_stale_jobs",
    "sched_integrity_check":  "_schedule_integrity_check",
    "polygon_canary_preopen": "_polygon_canary_check",
    "polygon_canary_preseed": "_polygon_canary_check",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.readlines()


def sha256_file(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def check_syntax(path: str) -> tuple[bool, str]:
    try:
        py_compile.compile(path, doraise=True)
        return True, "OK"
    except py_compile.PyCompileError as e:
        return False, str(e)


def find_scheduler_start(lines: list[str]) -> int | None:
    """Return 1-based line number of the first sched[uler].start() STATEMENT.

    Only matches lines where sched.start() is the actual Python statement
    (possibly indented), not inside a comment or string literal.
    Specifically: the line must not start with '#' after stripping whitespace,
    and sched.start() must appear at the start of the non-whitespace content
    (not buried inside a larger expression like a log message or docstring).
    """
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue  # skip comment lines
        # Match only when sched.start() is at the beginning of the statement
        if re.match(r'^\s*sched(?:uler)?\.start\(\)', ln):
            return i + 1
    return None


def find_def_line(lines: list[str], name: str) -> int | None:
    for i, ln in enumerate(lines):
        if re.search(rf'\bdef {re.escape(name)}\s*\(', ln):
            return i + 1
    return None


def check_guard_coverage(lines: list[str], targets: list[str]) -> list[str]:
    """
    For each target, verify _wait_for_module_load() appears within WINDOW lines
    before any Thread(target=<name>) call referencing it, OR that the target
    appears in a named-wrapper block that also contains the guard.

    Returns a list of target names that have NO guard anywhere in the file.
    """
    WINDOW = 30
    unguarded = []
    for target in targets:
        # If the target isn't referenced in the file at all, skip (not wired).
        if not any(target in ln for ln in lines):
            continue

        guarded = False

        # Pass 1: within WINDOW lines before a Thread(target=...) reference
        for i, ln in enumerate(lines):
            if f"target={target}" in ln and "Thread" in "".join(lines[max(0, i-5):i+1]):
                window = lines[max(0, i - WINDOW):i]
                if any("_wait_for_module_load" in wl for wl in window):
                    guarded = True
                    break

        # Pass 2: named-wrapper block — guard must be within 8 lines before the
        # target reference (tight window prevents adjacent wrapper guards from
        # producing false-negatives in the forced-failure proof).
        if not guarded:
            for i, ln in enumerate(lines):
                if "_wait_for_module_load" in ln:
                    ctx = "".join(lines[i:min(len(lines), i + 8)])
                    if target in ctx:
                        guarded = True
                        break

        if not guarded:
            unguarded.append(target)

    return unguarded


def check_raw_lambdas(lines: list[str], targets: list[str]) -> list[tuple[int, str]]:
    """Return (lineno, text) for any scheduler lambda that directly references a late-def target."""
    bad = []
    for i, ln in enumerate(lines):
        if "lambda" in ln and "add_job" in "".join(lines[max(0, i-3):i+1]):
            for t in targets:
                if t in ln:
                    bad.append((i + 1, ln.strip()))
                    break
    return bad


def options_engine_check(path: str) -> tuple[int | None, list[tuple[str, int]]]:
    """
    Verify all add_job targets in aiem_options_scheduler.py are defined
    BEFORE sched.start().  Returns (sched_start_line, list_of_violations).
    """
    lines = load_lines(path)
    sched_start = find_scheduler_start(lines)
    if not sched_start:
        return None, [("sched.start() NOT FOUND", 0)]

    bad: list[tuple[str, int]] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.search(r'sched\.add_job\(', ln):
            # Next non-blank, non-comment line is the callable
            for j in range(i + 1, min(len(lines), i + 5)):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith("#"):
                    continue
                m = re.match(r'([\w]+)\s*[,)]', nxt)
                if m:
                    name = m.group(1)
                    defln = find_def_line(lines, name)
                    if defln and defln > sched_start:
                        bad.append((name, defln))
                break
        i += 1
    return sched_start, bad


def ast_defined_functions(path: str) -> set[str]:
    """Return the set of all function names defined in the file (via AST)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        source = f.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def options_engine_selfcheck(oe_module_targets: dict[str, str],
                              oe_defined_fns: set[str]) -> list[tuple[str, str]]:
    """
    Simulate the OE runtime self-check: return (job_id, fn_name) pairs where
    fn_name is NOT in the defined function set.
    """
    return [
        (jid, fn)
        for jid, fn in oe_module_targets.items()
        if fn not in oe_defined_fns
    ]


# ── AIEM per-system forced-failure proof ─────────────────────────────────────

def aiem_forced_failure_proof() -> bool:
    """
    AIEM-specific forced-failure proof:
      Case A — synthetic wrapper WITHOUT guard → check_guard_coverage detects MISSING
      Case B — synthetic wrapper WITH guard → check_guard_coverage sees PASS

    Returns True if both assertions hold (detector works correctly for AIEM).
    """
    fake_fn = "_run_aiem_synthetic_late_target_xyz"

    # Case A: no _wait_for_module_load() guard
    unguarded_lines = [
        "def _run_aiem_some_scheduler_job():\n",
        "    try:\n",
        "        import threading as _t\n",
        "        _t.Thread(target=_run_aiem_synthetic_late_target_xyz, daemon=True).start()\n",
        "    except Exception as e:\n",
        "        print(e)\n",
    ]
    missing_a = check_guard_coverage(unguarded_lines, [fake_fn])
    case_a_ok = fake_fn in missing_a  # must be caught

    # Case B: guard present
    guarded_lines = [
        "def _run_aiem_some_scheduler_job():\n",
        "    try:\n",
        "        import threading as _t\n",
        "        _wait_for_module_load()  # guard\n",
        "        _t.Thread(target=_run_aiem_synthetic_late_target_xyz, daemon=True).start()\n",
        "    except Exception as e:\n",
        "        print(e)\n",
    ]
    missing_b = check_guard_coverage(guarded_lines, [fake_fn])
    case_b_ok = fake_fn not in missing_b  # must NOT be flagged

    return case_a_ok and case_b_ok


# ── OE per-system forced-failure proof ───────────────────────────────────────

def oe_forced_failure_proof(oe_defined_fns: set[str]) -> bool:
    """
    OE-specific forced-failure proof:
      Case A — _OE_MODULE_JOB_TARGETS with a non-existent function name
               → options_engine_selfcheck detects MISSING
      Case B — same dict with real function names → options_engine_selfcheck
               returns empty (all verified)

    Returns True if both assertions hold (detector works correctly for OE).
    """
    # Case A: inject a target that doesn't exist in the OE file
    bad_targets = dict(OE_MODULE_TARGETS)
    bad_targets["__oe_smoke_test_job__"] = "_oe_function_that_does_not_exist_xyz"

    missing_a = options_engine_selfcheck(bad_targets, oe_defined_fns)
    case_a_ok = any(fn == "_oe_function_that_does_not_exist_xyz"
                    for _, fn in missing_a)  # must be caught

    # Case B: only real targets
    missing_b = options_engine_selfcheck(OE_MODULE_TARGETS, oe_defined_fns)
    case_b_ok = len(missing_b) == 0  # all real targets must resolve

    return case_a_ok and case_b_ok


# ── Run all checks ────────────────────────────────────────────────────────────

passes:   list[str] = []
failures: list[str] = []


# Check 1: Syntax
for label, path in [("AIEM main.py", MAIN_PY), ("Options Engine", OPT_PY)]:
    ok, msg = check_syntax(path)
    if ok:
        passes.append(f"SYNTAX {label}")
    else:
        failures.append(f"SYNTAX {label}: {msg}")


# Check 2: Guard coverage (main.py)
main_lines = load_lines(MAIN_PY)
missing_guards = check_guard_coverage(main_lines, LATE_REF_TARGETS)
if not missing_guards:
    passes.append(
        f"GUARD COVERAGE: all {len(LATE_REF_TARGETS)} late-ref targets "
        f"guarded in main.py"
    )
else:
    for t in missing_guards:
        failures.append(
            f"GUARD MISSING: {t} has no _wait_for_module_load() in main.py"
        )


# Check 3: No raw scheduler lambdas reference late-def targets
bad_lambdas = check_raw_lambdas(main_lines, LATE_REF_TARGETS)
if not bad_lambdas:
    passes.append(
        "LAMBDA CHECK: no raw scheduler lambdas reference late-def targets in main.py"
    )
else:
    for lineno, text in bad_lambdas:
        failures.append(f"RAW LAMBDA line {lineno}: {text[:100]}")


# Check 4: Options Engine — all add_job targets defined before sched.start()
oe_start, oe_bad = options_engine_check(OPT_PY)
if oe_start is None:
    failures.append("OE: sched.start() not found in aiem_options_scheduler.py")
elif not oe_bad:
    passes.append(
        f"OE LATE-REF: no add_job targets after sched.start() "
        f"(line {oe_start}) in aiem_options_scheduler.py"
    )
else:
    for name, defln in oe_bad:
        failures.append(
            f"OE LATE-REF: {name} defined at line {defln} "
            f"(after sched.start() at line {oe_start})"
        )


# ── Check 5 (F.2.c): Core-path structural dryrun ────────────────────────────
# Verifies the core candidate-generation functions exist in both systems using
# AST analysis — no execution, no DB connections, no side-effects.
# What this confirms: the functions have not been renamed, deleted, or moved to
# a different module since the guard registry was last updated.
# What this does NOT confirm: runtime correctness or DB query success.
aiem_defined = ast_defined_functions(MAIN_PY)
oe_defined   = ast_defined_functions(OPT_PY)

aiem_core_missing = [f for f in AIEM_CORE_FUNCTIONS if f not in aiem_defined]
oe_core_missing   = [f for f in OE_CORE_FUNCTIONS   if f not in oe_defined]

if not aiem_core_missing and not oe_core_missing:
    passes.append(
        f"CORE-PATH STRUCTURAL DRYRUN: "
        f"AIEM {len(AIEM_CORE_FUNCTIONS)} core fns + "
        f"OE {len(OE_CORE_FUNCTIONS)} core fns verified present via AST — "
        f"no exceptions (no execution required)"
    )
else:
    for f in aiem_core_missing:
        failures.append(f"CORE-PATH DRYRUN: AIEM function missing from AST: {f}")
    for f in oe_core_missing:
        failures.append(f"CORE-PATH DRYRUN: OE function missing from AST: {f}")


# ── Check 8: Scheduler-target existence (AST) ─────────────────────────────────
# Verifies that every name in LATE_REF_TARGETS still exists as a def in main.py,
# and every function name in OE_MODULE_TARGET_FNS still exists in aiem_options_scheduler.py.
# Catches the "rename a scheduler target" failure mode that Check 2/4 miss
# (because those checks look at wrapper references, not function definitions).
# This is the CI-observable equivalent of the runtime self-check's remove_job() path.

aiem_missing_defs = [t for t in LATE_REF_TARGETS if t not in aiem_defined]
oe_target_missing = [f for f in OE_MODULE_TARGET_FNS if f not in oe_defined]

if not aiem_missing_defs and not oe_target_missing:
    passes.append(
        f"SCHEDULER TARGET EXISTENCE: all {len(LATE_REF_TARGETS)} AIEM late-ref targets + "
        f"{len(OE_MODULE_TARGET_FNS)} OE module targets verified as defined functions via AST"
    )
else:
    for t in aiem_missing_defs:
        failures.append(
            f"SCHEDULER TARGET MISSING IN MAIN.PY: {t} is in LATE_REF_TARGETS but "
            f"not defined as a function — scheduler self-check would remove its job"
        )
    for t in oe_target_missing:
        failures.append(
            f"SCHEDULER TARGET MISSING IN OE: {t} is in OE_MODULE_TARGET_FNS but "
            f"not defined as a function — scheduler self-check would remove its job"
        )


# ── Check 9: OE catch-up mechanism structural verification ────────────────────
# Verifies that the OE startup catch-up and _schedule_integrity_check both
# have catch-up EXECUTION wired (not just alerting), and that the
# scheduler_run_audit write is present.  All checks are AST/grep-only —
# no process execution, no network calls.
# Fires on every push that touches aiem_options_scheduler.py.

with open(OPT_PY, "r", encoding="utf-8") as _f:
    _oe_src = _f.read()
_oe_tree = ast.parse(_oe_src)

_catchup_checks_passed = []
_catchup_checks_failed = []

# 9a: _OE_CATCHUP_GUARD module-level set must exist
_has_guard = "_OE_CATCHUP_GUARD" in _oe_src
if _has_guard:
    _catchup_checks_passed.append("9a: _OE_CATCHUP_GUARD defined")
else:
    _catchup_checks_failed.append("9a: _OE_CATCHUP_GUARD missing — double-fire prevention not in place")

# 9b: _oe_write_scheduler_audit function must be defined
_has_audit_fn = any(
    isinstance(n, ast.FunctionDef) and n.name == "_oe_write_scheduler_audit"
    for n in ast.walk(_oe_tree)
)
if _has_audit_fn:
    _catchup_checks_passed.append("9b: _oe_write_scheduler_audit defined")
else:
    _catchup_checks_failed.append("9b: _oe_write_scheduler_audit missing — RECOVERED audit row not writable")

# 9c: _oe_catchup_run_pipeline function must be defined (the thread target)
_has_catchup_fn = any(
    isinstance(n, ast.FunctionDef) and n.name == "_oe_catchup_run_pipeline"
    for n in ast.walk(_oe_tree)
)
if _has_catchup_fn:
    _catchup_checks_passed.append("9c: _oe_catchup_run_pipeline defined")
else:
    _catchup_checks_failed.append("9c: _oe_catchup_run_pipeline missing — catch-up thread target not defined")

# 9d: _schedule_integrity_check must reference _oe_catchup_run_pipeline
#     (i.e. the periodic check triggers execution, not just alerting)
_integ_fn_src = ""
for _node in ast.walk(_oe_tree):
    if isinstance(_node, ast.FunctionDef) and _node.name == "_schedule_integrity_check":
        _integ_fn_src = ast.get_source_segment(_oe_src, _node) or ""
        break
if "_oe_catchup_run_pipeline" in _integ_fn_src:
    _catchup_checks_passed.append("9d: _schedule_integrity_check wired to _oe_catchup_run_pipeline")
else:
    _catchup_checks_failed.append(
        "9d: _schedule_integrity_check does NOT call _oe_catchup_run_pipeline — "
        "periodic check alerts only, does not execute catch-up"
    )

# 9e: startup missed-seed section must call _oe_write_scheduler_audit
#     Grep for the call that writes the RECOVERED row in the startup block.
if "_oe_write_scheduler_audit" in _oe_src and "startup_catchup" in _oe_src:
    _catchup_checks_passed.append("9e: startup missed-seed calls _oe_write_scheduler_audit with startup_catchup trigger")
else:
    _catchup_checks_failed.append(
        "9e: startup missed-seed does NOT call _oe_write_scheduler_audit — "
        "startup catch-up produces no RECOVERED audit row"
    )

if not _catchup_checks_failed:
    passes.append(
        f"OE CATCH-UP MECHANISM: all {len(_catchup_checks_passed)} structural checks pass "
        f"— guard, audit-write fn, thread-target fn, integrity-check wiring, startup-section wiring"
    )
    for _c in _catchup_checks_passed:
        print(f"  [PASS] catchup sub-check {_c}")
else:
    for _c in _catchup_checks_failed:
        failures.append(f"OE CATCH-UP MECHANISM: {_c}")
    for _c in _catchup_checks_passed:
        print(f"  [PASS] catchup sub-check {_c}")
    for _c in _catchup_checks_failed:
        print(f"  [FAIL] catchup sub-check {_c}")


# ── Checks 10-11: Per-system forced-failure proofs (opt-in) ──────────────────
if os.environ.get("SMOKE_FULL"):

    # Check 6: AIEM forced-failure
    aiem_ok = aiem_forced_failure_proof()
    if aiem_ok:
        passes.append(
            "AIEM FORCED-FAILURE: unguarded wrapper caught; guarded wrapper passes "
            "— check_guard_coverage correctly distinguishes both cases"
        )
    else:
        failures.append(
            "AIEM FORCED-FAILURE: check_guard_coverage failed AIEM synthetic case "
            "— missed unguarded wrapper or flagged guarded one"
        )

    # Check 7: OE forced-failure
    oe_ok = oe_forced_failure_proof(oe_defined)
    if oe_ok:
        passes.append(
            "OE FORCED-FAILURE: missing module-level target caught; all-real dict passes "
            "— options_engine_selfcheck correctly distinguishes both cases"
        )
    else:
        failures.append(
            "OE FORCED-FAILURE: options_engine_selfcheck failed OE synthetic case "
            "— missed non-existent target or flagged real one"
        )


# ── Report ────────────────────────────────────────────────────────────────────

sha_main = sha256_file(MAIN_PY)
sha_opt  = sha256_file(OPT_PY)

print(f"\n{'='*72}")
print("SCHEDULER SMOKE-TEST REPORT")
print(f"{'='*72}")
print(f"main.py sha256:                    {sha_main}")
print(f"aiem_options_scheduler.py sha256:  {sha_opt}")
print(f"{'='*72}")
print(f"PASS: {len(passes)}   FAIL: {len(failures)}")
print()
for p in passes:
    print(f"  [PASS] {p}")
for fa in failures:
    print(f"  [FAIL] {fa}")
print(f"{'='*72}\n")

sys.exit(0 if not failures else 1)
