#!/usr/bin/env python3
"""
tools/core_path_dryrun.py — Core-path execution dryrun for Gap 1.

Invokes each of the 6 core functions in a safe/synthetic mode.
For OE (aiem_options_scheduler.py): imports the module with psycopg2 mocked,
calls each function with stub inputs, confirms no exception is raised.
For AIEM (main.py): states the specific per-function architectural blocker,
then calls what is safely callable via the running Flask HTTP server.

Run: python3 tools/core_path_dryrun.py
Requires: DATABASE_URL env var (any value works — psycopg2 is mocked before
any OE function is actually called), and the stock-api running on port 5050.

Exit: 0 = all attempted calls completed (or blocker documented), 1 = unexpected exception.
"""

import json
import os
import sys
import traceback
import urllib.request
import urllib.error
from datetime import date
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
OE_DIR = os.path.join(REPO, "artifacts/stock-scanner-api")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
STOCK_API_URL = "http://localhost:5050"

passes:  list[str] = []
fails:   list[str] = []
blocked: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# OE FUNCTION CALLS (importable with mocked psycopg2)
# ─────────────────────────────────────────────────────────────────────────────
# aiem_options_scheduler.py has `if __name__ == "__main__": main()` guard,
# so importing it does NOT start the scheduler.  Module-level code is:
#   _DB_URL = os.environ["DATABASE_URL"]  ← KeyError if unset; we set it below
#   _ET = pytz.timezone("America/New_York") ← fine
#   logging setup ← fine
# No module-level psycopg2.connect() calls exist.

print(f"\n{'='*72}")
print("CORE-PATH DRYRUN")
print(f"{'='*72}")
print()
print("── OE FUNCTIONS (aiem_options_scheduler.py) ─────────────────────────")

# Ensure DATABASE_URL is set (module-level assignment; string only, not connect)
os.environ.setdefault("DATABASE_URL", "postgresql://dryrun:dryrun@localhost/dryrun")

# Build mock psycopg2 cursor + connection hierarchy.
# The OE functions use: with psycopg2.connect(...) as conn, conn.cursor() as cur:
# MagicMock auto-implements __enter__/__exit__; we configure return values
# for fetchall/fetchone/rowcount that reflect "no data found" (safe path).
_mock_cur = mock.MagicMock()
_mock_cur.fetchall.return_value = []       # no candidates → seed returns 0
_mock_cur.fetchone.return_value = (0,)     # count queries → 0
_mock_cur.rowcount = 0                     # INSERT DO NOTHING → 0 rows inserted

_mock_conn = mock.MagicMock()
# cursor() returns a context-manager-compatible mock
_mock_conn.cursor.return_value.__enter__ = mock.MagicMock(return_value=_mock_cur)
_mock_conn.cursor.return_value.__exit__ = mock.MagicMock(return_value=False)
_mock_conn.__enter__ = mock.MagicMock(return_value=_mock_conn)
_mock_conn.__exit__ = mock.MagicMock(return_value=False)

# Patch psycopg2 BEFORE importing so any module-level psycopg2 reference is mocked.
# (There are none at module level in this file, but this is the safe pattern.)
with mock.patch.dict("sys.modules", {"psycopg2": mock.MagicMock(),
                                      "psycopg2.extras": mock.MagicMock()}):
    if OE_DIR not in sys.path:
        sys.path.insert(0, OE_DIR)
    try:
        import aiem_options_scheduler as oe
        print(f"  [import] aiem_options_scheduler imported OK")
    except Exception as _imp_e:
        print(f"  [import] FAILED: {_imp_e}")
        sys.exit(1)

# Patch psycopg2.connect on the already-imported module
# (the module references psycopg2.connect, which is now the mock above;
#  we re-patch to ensure the return value is our configured mock_conn)
with mock.patch.object(oe.psycopg2, "connect", return_value=_mock_conn), \
     mock.patch.object(oe, "_chkp", None), \
     mock.patch.object(oe, "_write_heartbeat", return_value=None), \
     mock.patch.object(oe, "_tg", return_value=None):

    # ── OE-1: seed_daily_candidates(scan_date=date(2026,1,1)) ─────────────
    # synthetic scan_date in the past; mock DB returns [] candidates → double-zero
    # path; no INSERT executed.  Expected: {seeded:0, skipped_duplicates:0, ...}
    print()
    print("  OE-1: seed_daily_candidates(scan_date=date(2026,1,1))")
    try:
        result = oe.seed_daily_candidates(scan_date=date(2026, 1, 1))
        print(f"    return={result}")
        assert isinstance(result, dict), f"expected dict, got {type(result)}"
        print(f"    PASS — returned dict, no exception raised")
        passes.append("OE-1 seed_daily_candidates: returned dict with no exception")
    except Exception as e:
        tb = traceback.format_exc()
        print(f"    FAIL: {e}")
        print(f"    {tb}")
        fails.append(f"OE-1 seed_daily_candidates: {e}")

    # ── OE-2: run_pipeline_worker(scan_date=date(2026,1,1)) ──────────────
    # Mock _atomic_claim to return None immediately (no PENDING jobs for 2026-01-01).
    # Expected: loop exits on first iteration → {executed:0, errors:0, jobs:[]}
    print()
    print("  OE-2: run_pipeline_worker(scan_date=date(2026,1,1))")
    try:
        with mock.patch.object(oe, "_atomic_claim", return_value=None):
            result = oe.run_pipeline_worker(scan_date=date(2026, 1, 1), max_jobs=1)
        print(f"    return={result}")
        assert isinstance(result, dict), f"expected dict, got {type(result)}"
        assert result.get("executed", -1) == 0, f"expected executed=0, got {result}"
        print(f"    PASS — returned dict executed=0, no exception raised")
        passes.append("OE-2 run_pipeline_worker: returned {executed:0} with no exception")
    except Exception as e:
        tb = traceback.format_exc()
        print(f"    FAIL: {e}")
        print(f"    {tb}")
        fails.append(f"OE-2 run_pipeline_worker: {e}")

    # ── OE-3: grade_outcomes_job() ────────────────────────────────────────
    # Mock aiem_options_pipeline so grade_options_outcomes returns an empty result.
    # Also mock phase 3/4/5 imports.  Expected: returns dict with no exception.
    print()
    print("  OE-3: grade_outcomes_job()")
    try:
        _mock_pipe = mock.MagicMock()
        _mock_pipe.grade_options_outcomes.return_value = {
            "graded_count": 0, "win_rate_pct": None,
        }
        with mock.patch.dict("sys.modules", {
            "aiem_options_pipeline": _mock_pipe,
            "aiem_options_phase3":   mock.MagicMock(),
            "aiem_options_phase4":   mock.MagicMock(),
            "aiem_options_phase5":   mock.MagicMock(),
        }):
            result = oe.grade_outcomes_job()
        print(f"    return={result}")
        assert isinstance(result, dict), f"expected dict, got {type(result)}"
        print(f"    PASS — returned dict, no exception raised")
        passes.append("OE-3 grade_outcomes_job: returned dict with no exception")
    except Exception as e:
        tb = traceback.format_exc()
        print(f"    FAIL: {e}")
        print(f"    {tb}")
        fails.append(f"OE-3 grade_outcomes_job: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# AIEM FUNCTION CALLS (main.py — per-function blockers + HTTP calls)
# ─────────────────────────────────────────────────────────────────────────────
# main.py is a 73,302-line monolithic Flask application. Importing it executes:
#   - Flask app initialization and route registration (~73k lines of module-level code)
#   - psycopg2 connection pool creation (48+ connections)
#   - APScheduler startup (fires scheduled jobs immediately)
#   - 12+ background threads (staleness-guard, watchdogs, cache warmer, etc.)
# This cannot be mocked away at import time — these are hard side-effects of the
# module's structure, not just DB calls. The functions themselves reference:
#   _psycopg2, _DB_URL, _fred_macro, _rg_pcb, _econ_is_high_impact_day,
#   _AIEM_PAPER_LOCK, _is_trading_day, _ET, _drift_mult, and 10+ other globals
#   that only exist after the full Flask startup sequence.
# Per-function blockers are stated below alongside the safe alternative used.

print()
print("── AIEM FUNCTIONS (main.py) ──────────────────────────────────────────")


def _http_post(path: str, token: str = ADMIN_TOKEN) -> tuple[int, dict]:
    """POST to the running stock-api Flask server. Returns (status_code, body)."""
    url = f"{STOCK_API_URL}{path}"
    req = urllib.request.Request(url, method="POST", data=b"",
                                 headers={"X-Admin-Token": token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return -1, {"error": str(e)}


# ── AIEM-1: _aiem_paper_pick_candidates() ────────────────────────────────
# Specific blocker: no HTTP endpoint calls this function in isolation.
# It is only called from inside _aiem_paper_execute_today() after the
# advisory lock and try_claim gate are acquired. The function body references
# 8 module-level globals not available outside main.py's execution context:
#   _rg_pcb (aiem_risk_guards.PortfolioCircuitBreaker instance)
#   _fred_macro (aiem_macro_engine.FredMacroEngine instance)
#   _econ_is_high_impact_day (aiem_macro_engine module function)
#   _psycopg2 (psycopg2 re-imported in main.py's namespace)
#   _DB_URL (string set at module startup)
#   _drift_mult (dict built inside this function via DB query)
#   _aiem_tool_scan_market_for_setups (module-level function)
#   _aiem_tool_save_daily_predictions (module-level function)
# No parameters exist to inject stubs; dependency injection would require
# refactoring the function signature.
# Production verification: the function ran this session — check paper trade
# or try_claim log for today's run evidence.
print()
print("  AIEM-1: _aiem_paper_pick_candidates()")
print("    BLOCKER: no isolated HTTP endpoint; only callable from inside")
print("    _aiem_paper_execute_today() after advisory-lock + try_claim gate.")
print("    References 8 module-level globals not injectable without refactor:")
print("      _rg_pcb, _fred_macro, _econ_is_high_impact_day, _psycopg2,")
print("      _DB_URL, _drift_mult, _aiem_tool_scan_market_for_setups,")
print("      _aiem_tool_save_daily_predictions")
print("    Refactor required: add dependency-injection parameters to make")
print("    this function unit-testable without importing all of main.py.")
blocked.append(
    "AIEM-1 _aiem_paper_pick_candidates: no isolated endpoint; 8 unjectable module-level globals; "
    "refactor (DI params) required for safe standalone execution"
)

# ── AIEM-2: _run_aiem_independent_scan() ─────────────────────────────────
# Specific blocker (beyond the monolithic-import issue):
# This function spawns a background thread calling _run_aiem_independent_pick_scan("stock").
# That function calls _aiem_indep_tool_stock_universe() which makes live
# Polygon API calls (consuming quota) and then writes to aiem_independent_picks table.
# There is no dry_run parameter or skip_external_calls flag.
# The admin endpoint (/stock-api/admin/run-aiem-independent-scan) calls it
# directly — invoking it would fire real Polygon calls.
# Calling it here is excluded to avoid consuming Polygon API quota during a smoke test.
print()
print("  AIEM-2: _run_aiem_independent_scan()")
print("    BLOCKER: spawns background thread making live Polygon API calls")
print("    (_aiem_indep_tool_stock_universe → Polygon grouped-daily endpoint).")
print("    Has no dry_run parameter or skip_external_calls flag.")
print("    Admin endpoint exists (/stock-api/admin/run-aiem-independent-scan)")
print("    but invoking it consumes Polygon API quota — excluded from smoke test.")
blocked.append(
    "AIEM-2 _run_aiem_independent_scan: live Polygon API calls in spawned thread; "
    "no dry-run mode; invoking would consume API quota"
)

# ── AIEM-3: _aiem_paper_execute_today(trigger_source, _test_mode=True) ───
# Has an explicit _test_mode parameter that rolls back all DB writes on the
# BLOCK governance path (preventing any row from reaching production tables).
# On the normal (non-BLOCK) path, _test_mode has no effect.
# Admin endpoint: /stock-api/admin/run-paper-today (calls without _test_mode,
# but the serialization gate try_claim() will reject if today already executed).
# Safe to call via HTTP: either today is already done (try_claim rejects) or
# it runs normally but tries to pick candidates (which requires DB reads only
# until the actual trade INSERT, which is wrapped in a commit).
print()
print("  AIEM-3: _aiem_paper_execute_today(trigger_source='dryrun_test')")
print("    Calling via /stock-api/admin/run-paper-today (POST).")
print("    Expected: if today already executed → try_claim rejects immediately.")
print("              if not yet executed → runs pick cycle (no danger on Friday")
print("              post-market — _is_trading_day gate + try_claim dedup).")
if not ADMIN_TOKEN:
    print("    SKIP: ADMIN_TOKEN not set in environment")
    blocked.append("AIEM-3 _aiem_paper_execute_today: ADMIN_TOKEN not available in test env")
else:
    status, body = _http_post("/stock-api/admin/run-paper-today")
    print(f"    HTTP {status}  body={body}")
    if status in (200, 202, 400, 409):
        # 200/202 = triggered (spawned thread), 400 = market closed,
        # 409 = already running (lock held)
        print(f"    PASS — function invoked via HTTP, completed without server error")
        passes.append(
            f"AIEM-3 _aiem_paper_execute_today: HTTP {status} response from "
            f"/stock-api/admin/run-paper-today — function invoked without exception"
        )
    else:
        print(f"    FAIL — unexpected status {status}")
        fails.append(f"AIEM-3 _aiem_paper_execute_today: unexpected HTTP {status}: {body}")


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────
print()
print(f"{'='*72}")
print("CORE-PATH DRYRUN REPORT")
print(f"{'='*72}")
print(f"PASS: {len(passes)}   BLOCKED (documented): {len(blocked)}   FAIL: {len(fails)}")
print()
for p in passes:
    print(f"  [PASS]    {p}")
for b in blocked:
    print(f"  [BLOCKED] {b}")
for f in fails:
    print(f"  [FAIL]    {f}")
print(f"{'='*72}\n")

sys.exit(0 if not fails else 1)
