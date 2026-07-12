"""
d3_negctl_fulltest_subprocess.py
=================================
Minimal inner script for the D3 negative-control full test.
Designed to be run as a subprocess by d3_negctl_harness.py --full-test.

This script patches socket.socket.bind at the lowest level so that main.py's
module-level make_server() call (early port bind) does not collide with the
already-running stock-api workflow on port 5050.  The patch is applied BEFORE
any Flask-related import so no port is actually bound.

All D3 writes go to d3_test_isolation via the DATABASE_URL already set in the
environment by the parent process (harness).
"""
import os, sys, time

# ── 0. Verify DATABASE_URL already has the test search_path ──────────────────
_db_url = os.environ.get("DATABASE_URL", "")
if "d3_test_isolation" not in _db_url:
    print("SUBPROCESS_ERROR: DATABASE_URL does not contain d3_test_isolation")
    sys.exit(1)

print(f"[SUB] DATABASE_URL has d3_test_isolation ✓")

# ── 1. Intercept socket.bind so early-port-bind in main.py is a no-op ────────
import socket as _socket
_orig_bind = _socket.socket.bind

def _intercepted_bind(self, addr):
    if isinstance(addr, (list, tuple)) and len(addr) == 2 and addr[1] in (5050, 8080, 5000, 5001):
        print(f"[SUB] Port bind intercepted: {addr} → no-op (harness isolation)")
        return
    return _orig_bind(self, addr)

_socket.socket.bind = _intercepted_bind
print("[SUB] socket.bind intercepted for ports 5050/8080/5000/5001")

# ── 2. Also stub wsgiref.simple_server.make_server pre-import ─────────────────
import wsgiref.simple_server as _wss
_wss.make_server = lambda *a, **kw: type("_FakeSrv", (), {
    "socket": None, "serve_forever": lambda *a, **kw: None,
    "shutdown": lambda *a, **kw: None, "server_close": lambda *a, **kw: None,
})()
print("[SUB] wsgiref.simple_server.make_server stubbed")

# ── 3. Import the real main module ────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
print("[SUB] Importing main (heavy — may take several seconds)…")
try:
    import main as _main
    print("[SUB] main imported ✓")
except SystemExit as e:
    print(f"[SUB] main import raised SystemExit({e}) — check port stubs")
    sys.exit(1)
except Exception as e:
    print(f"[SUB] main import failed: {e!r}")
    raise

# ── 4. Patch _g0_read_config to force ENFORCE+PAUSED ─────────────────────────
import aiem_diagram3_governance as _d3g
def _patched_g0_read_config(force=False):
    return {"mode": "ENFORCE", "state": "PAUSED", "error": None, "ts": time.time()}
_d3g._g0_read_config = _patched_g0_read_config
print("[SUB] _g0_read_config patched → ENFORCE / PAUSED")

# ── 5. Capture pre-test counts ────────────────────────────────────────────────
import psycopg2

# Use ORIGINAL URL (without options) to read both schemas honestly
_ORIG_URL = _db_url
# Reconstruct original URL by removing the options param
import urllib.parse
_p = urllib.parse.urlparse(_ORIG_URL)
_qs = dict(urllib.parse.parse_qsl(_p.query, keep_blank_values=True))
_qs.pop("options", None)
_CLEAN_URL = _p._replace(query=urllib.parse.urlencode(_qs, quote_via=urllib.parse.quote)).geturl()

def _count(schema, table):
    conn = psycopg2.connect(_CLEAN_URL, connect_timeout=5)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    return n

pre_d3_req_test  = _count("d3_test_isolation", "d3_governance_requests")
pre_d3_req_pub   = _count("public", "d3_governance_requests")
pre_d3_gel_test  = _count("d3_test_isolation", "d3_governance_event_links")
pre_d3_gel_pub   = _count("public", "d3_governance_event_links")
pre_d3_dec_test  = _count("d3_test_isolation", "d3_governance_decisions")
pre_d3_dec_pub   = _count("public", "d3_governance_decisions")
pre_paper_trades = _count("public", "aiem_paper_trades")
pre_exec_log     = _count("public", "aiem_paper_execution_log")

print(f"\n[SUB][PRE] d3_test_isolation.d3_governance_requests = {pre_d3_req_test}")
print(f"[SUB][PRE] public.d3_governance_requests           = {pre_d3_req_pub}")
print(f"[SUB][PRE] d3_test_isolation.d3_governance_event_links = {pre_d3_gel_test}")
print(f"[SUB][PRE] public.d3_governance_event_links            = {pre_d3_gel_pub}")
print(f"[SUB][PRE] d3_test_isolation.d3_governance_decisions = {pre_d3_dec_test}")
print(f"[SUB][PRE] public.d3_governance_decisions             = {pre_d3_dec_pub}")
print(f"[SUB][PRE] public.aiem_paper_trades          = {pre_paper_trades}")
print(f"[SUB][PRE] public.aiem_paper_execution_log   = {pre_exec_log}")

# ── 6. Call _aiem_paper_execute_today ─────────────────────────────────────────
print("\n[SUB][CALL] _aiem_paper_execute_today(trigger_source='directive5_fulltest')")
try:
    result = _main._aiem_paper_execute_today(trigger_source="directive5_fulltest")
except Exception as exc:
    print(f"[SUB][CALL] raised: {exc!r}")
    result = {"exception": str(exc)}

print(f"[SUB][RESULT] {result}")

# ── 7. Capture post-test counts ───────────────────────────────────────────────
post_d3_req_test  = _count("d3_test_isolation", "d3_governance_requests")
post_d3_req_pub   = _count("public", "d3_governance_requests")
post_d3_gel_test  = _count("d3_test_isolation", "d3_governance_event_links")
post_d3_gel_pub   = _count("public", "d3_governance_event_links")
post_d3_dec_test  = _count("d3_test_isolation", "d3_governance_decisions")
post_d3_dec_pub   = _count("public", "d3_governance_decisions")
post_paper_trades = _count("public", "aiem_paper_trades")
post_exec_log     = _count("public", "aiem_paper_execution_log")

print(f"\n[SUB][POST] d3_test_isolation.d3_governance_requests = {post_d3_req_test}")
print(f"[SUB][POST] public.d3_governance_requests            = {post_d3_req_pub}")
print(f"[SUB][POST] d3_test_isolation.d3_governance_event_links = {post_d3_gel_test}")
print(f"[SUB][POST] public.d3_governance_event_links             = {post_d3_gel_pub}")
print(f"[SUB][POST] d3_test_isolation.d3_governance_decisions = {post_d3_dec_test}")
print(f"[SUB][POST] public.d3_governance_decisions             = {post_d3_dec_pub}")
print(f"[SUB][POST] public.aiem_paper_trades         = {post_paper_trades}")
print(f"[SUB][POST] public.aiem_paper_execution_log  = {post_exec_log}")

# ── 8. Verdict ────────────────────────────────────────────────────────────────
print("\n[SUB][DELTA]")
all_pass = True

checks = [
    ("d3_test_isolation.d3_governance_requests",   post_d3_req_test  - pre_d3_req_test,  ">=1"),
    ("d3_test_isolation.d3_governance_event_links",post_d3_gel_test  - pre_d3_gel_test,  ">=1"),
    ("d3_test_isolation.d3_governance_decisions",  post_d3_dec_test  - pre_d3_dec_test,  ">=1"),
    ("public.d3_governance_requests",              post_d3_req_pub   - pre_d3_req_pub,   "==0"),
    ("public.d3_governance_event_links",           post_d3_gel_pub   - pre_d3_gel_pub,   "==0"),
    ("public.d3_governance_decisions",             post_d3_dec_pub   - pre_d3_dec_pub,   "==0"),
    ("public.aiem_paper_trades",                   post_paper_trades - pre_paper_trades,  "==0"),
]
for name, delta, expectation in checks:
    ok = (delta >= 1 if expectation == ">=1" else delta == 0)
    sign = "+" if delta > 0 else ""
    verdict = "OK" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  {name}: {sign}{delta}  [{verdict} — expected {expectation}]")

blocked_ok = isinstance(result, dict) and result.get("blocked") is True
if not blocked_ok:
    all_pass = False
    print(f"\n  result.blocked: FAIL — expected True, got: {result.get('blocked') if isinstance(result,dict) else result}")
else:
    print(f"\n  result.blocked: True  [OK]")

blocked_g0_in_log = (post_exec_log > pre_exec_log)
print(f"  aiem_paper_execution_log BLOCKED_G0 row: {'written (+' + str(post_exec_log-pre_exec_log) + ')' if blocked_g0_in_log else '0 (not written)'}")

print(f"\n[SUB][VERDICT] {'PASS' if all_pass else 'FAIL'}")
sys.exit(0 if all_pass else 1)
