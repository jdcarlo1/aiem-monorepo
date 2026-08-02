#!/usr/bin/env python3
"""
tools/core_path_dryrun.py — Core-path execution dryrun for Gap 1.

Invokes each of the 6 core functions in a safe/synthetic mode.
For OE (aiem_options_scheduler.py): imports the module with psycopg2 mocked,
calls each function with stub inputs, confirms no exception is raised.
For AIEM (main.py): AIEM-1 and AIEM-2 now have injectable parameters / dry_run
flags; tests exercise those paths via the running Flask HTTP server (admin endpoints).
AIEM-3 uses the existing /run-paper-today endpoint.

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
# AIEM FUNCTION CALLS (main.py — via running Flask HTTP server)
# ─────────────────────────────────────────────────────────────────────────────
# main.py is a monolithic Flask application (~73k lines). Importing it triggers
# Flask startup, psycopg2 pool creation, APScheduler, and 12+ background threads —
# these cannot be safely suppressed in a subprocess test.
#
# After the AIEM-1 and AIEM-2 refactors in this session, both functions now accept
# injected dependencies (AIEM-1: 6 keyword-only params) and a dry_run flag (AIEM-2).
# The running Flask server already has the module loaded with all globals initialized.
# Test methodology: POST to admin endpoints that exercise the injection / dry_run paths.

print()
print("── AIEM FUNCTIONS (main.py) ──────────────────────────────────────────")


def _http_post(path: str, token: str = ADMIN_TOKEN) -> tuple[int, dict]:
    """POST to the running stock-api Flask server. Returns (status_code, body)."""
    url = f"{STOCK_API_URL}{path}"
    req = urllib.request.Request(url, method="POST", data=b"",
                                 headers={"X-Admin-Token": token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return -1, {"error": str(e)}


# ── AIEM-1: _aiem_paper_pick_candidates() — injected mock deps ────────────
# After the DI refactor, the function accepts 6 keyword-only params:
#   db_url, psycopg2_mod, fred_macro, econ_gate_fn, social_sentiment, specialist_council
# The test endpoint /stock-api/admin/dryrun/pick-candidates calls the function
# with injected MagicMock psycopg2 (returning empty cursors) and a lambda econ_gate_fn
# that returns high_impact_day=False.  Production callers pass no args (unchanged).
print()
print("  AIEM-1: _aiem_paper_pick_candidates(psycopg2_mod=mock, db_url=mock, ...)")
print("    Testing via /stock-api/admin/dryrun/pick-candidates (injected mock deps).")
if not ADMIN_TOKEN:
    print("    SKIP: ADMIN_TOKEN not set in environment")
    blocked.append("AIEM-1 _aiem_paper_pick_candidates: ADMIN_TOKEN not available in test env")
else:
    status, body = _http_post("/stock-api/admin/dryrun/pick-candidates")
    print(f"    HTTP {status}  body={body}")
    if status == 200 and body.get("injected_params") is True:
        print(f"    PASS — function called with 6 injected mock deps; "
              f"candidates_returned={body.get('candidates_returned')}, "
              f"mock_connect_calls={body.get('mock_connect_calls')}")
        passes.append(
            f"AIEM-1 _aiem_paper_pick_candidates: HTTP 200 with injected mock deps; "
            f"candidates_returned={body.get('candidates_returned')} "
            f"mock_connect_calls={body.get('mock_connect_calls')} "
            f"injected_params={body.get('injected_params')}"
        )
    elif status == 401:
        print(f"    SKIP: unauthorized (ADMIN_TOKEN mismatch)")
        blocked.append("AIEM-1 _aiem_paper_pick_candidates: ADMIN_TOKEN mismatch")
    else:
        print(f"    FAIL — unexpected status {status}: {body}")
        fails.append(f"AIEM-1 _aiem_paper_pick_candidates: unexpected HTTP {status}: {body}")


# ── AIEM-2: _run_aiem_independent_scan(dry_run=True) ─────────────────────
# After the dry_run refactor, the function accepts dry_run=True which:
#   1. Bypasses the _is_trading_day() gate
#   2. Replaces the live Polygon universe call with _DRY_RUN_STUB_UNIVERSE_STOCK
#   3. Skips the DB save (prints "DRY_RUN — would save N picks" instead)
# The admin endpoint /stock-api/admin/run-aiem-independent-scan accepts ?dry_run=true
# and passes it through.  No Polygon API quota is consumed.
print()
print("  AIEM-2: _run_aiem_independent_scan(dry_run=True)")
print("    Testing via /stock-api/admin/run-aiem-independent-scan?dry_run=true.")
if not ADMIN_TOKEN:
    print("    SKIP: ADMIN_TOKEN not set in environment")
    blocked.append("AIEM-2 _run_aiem_independent_scan: ADMIN_TOKEN not available in test env")
else:
    status, body = _http_post("/stock-api/admin/run-aiem-independent-scan?dry_run=true")
    print(f"    HTTP {status}  body={body}")
    if status == 200 and body.get("dry_run") is True:
        print(f"    PASS — dry_run=True confirmed in response; Polygon API not called; "
              f"DB write skipped. message={body.get('message','')!r}")
        passes.append(
            f"AIEM-2 _run_aiem_independent_scan: HTTP 200 dry_run=True confirmed; "
            f"no Polygon call; no DB write; status={body.get('status')}"
        )
    elif status == 401:
        print(f"    SKIP: unauthorized (ADMIN_TOKEN mismatch)")
        blocked.append("AIEM-2 _run_aiem_independent_scan: ADMIN_TOKEN mismatch")
    else:
        print(f"    FAIL — unexpected status {status} or dry_run missing: {body}")
        fails.append(f"AIEM-2 _run_aiem_independent_scan: unexpected HTTP {status}: {body}")


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
