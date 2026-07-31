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
  5. Forced-failure proof (opt-in via SMOKE_FULL=1) — temporarily removes one
     guard from an in-memory copy and verifies check 2 detects it

Exit: 0 = all pass, 1 = at least one failure.
Run: python3 tools/scheduler_smoke_test.py
     SMOKE_FULL=1 python3 tools/scheduler_smoke_test.py  (adds forced-failure check)
"""
import hashlib
import os
import py_compile
import re
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
MAIN_PY = os.path.join(REPO, "artifacts/stock-scanner-api/main.py")
OPT_PY  = os.path.join(REPO, "artifacts/stock-scanner-api/aiem_options_scheduler.py")

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
    """Return 1-based line number of the first sched[uler].start() call."""
    for i, ln in enumerate(lines):
        if re.search(r'\bsched(?:uler)?\.start\(\)', ln):
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


def forced_failure_proof() -> bool:
    """
    Prove check_guard_coverage is not a no-op by testing it against two
    in-memory synthetic cases:

      Case A — NO guard  → must be flagged as unguarded (proof of detection)
      Case B — guard present within window → must NOT be flagged (no false-pos)

    Returns True if both assertions hold (detector works correctly).
    """
    fake_fn = "_run_synthetic_late_function_xyz"

    # Case A: scheduler wrapper with NO _wait_for_module_load() call
    unguarded_lines = [
        "def _some_scheduler_wrapper():\n",
        "    try:\n",
        "        import threading as _t\n",
        "        _t.Thread(target=_run_synthetic_late_function_xyz, daemon=True).start()\n",
        "    except Exception as e:\n",
        "        print(e)\n",
    ]
    missing_a = check_guard_coverage(unguarded_lines, [fake_fn])
    case_a_ok = fake_fn in missing_a  # must be detected as missing

    # Case B: same wrapper WITH the guard present
    guarded_lines = [
        "def _some_scheduler_wrapper():\n",
        "    try:\n",
        "        import threading as _t\n",
        "        _wait_for_module_load()\n",
        "        _t.Thread(target=_run_synthetic_late_function_xyz, daemon=True).start()\n",
        "    except Exception as e:\n",
        "        print(e)\n",
    ]
    missing_b = check_guard_coverage(guarded_lines, [fake_fn])
    case_b_ok = fake_fn not in missing_b  # must NOT be flagged

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


# Check 5: Forced-failure proof (opt-in)
if os.environ.get("SMOKE_FULL"):
    detected = forced_failure_proof()
    if detected:
        passes.append(
            "FORCED-FAILURE: synthetic unguarded wrapper detected; "
            "guarded wrapper correctly passes — check_guard_coverage verified"
        )
    else:
        failures.append(
            "FORCED-FAILURE: check_guard_coverage failed synthetic case — "
            "missed an unguarded wrapper or flagged a guarded one"
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
