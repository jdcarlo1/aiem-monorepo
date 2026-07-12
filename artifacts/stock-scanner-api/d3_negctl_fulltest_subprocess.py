"""
d3_negctl_fulltest_subprocess.py  (rev2)
=========================================
Inner script for D3 negative-control full test — run via subprocess by
d3_negctl_harness.py --full-test.

Changes vs rev1:
  - Writes structured result to /tmp/d3_negctl_result.json before exit
    so the parent can read evidence even after os._exit()
  - Uses os._exit(code) instead of sys.exit(code) — bypasses Python's
    atexit / threading.join() shutdown sequence so the process terminates
    immediately regardless of non-daemon threads started by main.py's
    APScheduler, cache warmers, or startup scans.
  - Streams milestone timestamps to /tmp/d3_negctl_progress.log so the
    parent can see how far the subprocess got if it is killed by timeout.
"""
import os, sys, time, json, datetime

PROGRESS_LOG = "/tmp/d3_negctl_progress.log"
RESULT_FILE  = "/tmp/d3_negctl_result.json"

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def _log(msg):
    line = f"{_ts()} {msg}"
    print(line, flush=True)
    with open(PROGRESS_LOG, "a") as f:
        f.write(line + "\n")

def _write_result(d):
    with open(RESULT_FILE, "w") as f:
        json.dump(d, f, indent=2, default=str)

# ── 0. Verify DATABASE_URL already has the test search_path ──────────────────
_db_url = os.environ.get("DATABASE_URL", "")
if "d3_test_isolation" not in _db_url:
    _log("SUBPROCESS_ERROR: DATABASE_URL does not contain d3_test_isolation")
    _write_result({"verdict": "FAIL", "reason": "DATABASE_URL missing d3_test_isolation"})
    os._exit(1)

_log(f"DATABASE_URL has d3_test_isolation search_path ✓")

# ── 1. Intercept socket.bind so early-port-bind in main.py is a no-op ────────
import socket as _socket
_orig_bind = _socket.socket.bind

def _intercepted_bind(self, addr):
    if isinstance(addr, (list, tuple)) and len(addr) == 2 \
            and addr[1] in (5050, 8080, 5000, 5001):
        _log(f"[intercept] socket.bind({addr}) → no-op")
        return
    return _orig_bind(self, addr)

_socket.socket.bind = _intercepted_bind
_log("socket.bind intercepted for ports 5050/8080/5000/5001 ✓")

# ── 2. Stub wsgiref.make_server pre-import ─────────────────────────────────
import wsgiref.simple_server as _wss
_wss.make_server = lambda *a, **kw: type("_FakeSrv", (), {
    "socket": None, "serve_forever": lambda *a, **kw: None,
    "shutdown": lambda *a, **kw: None, "server_close": lambda *a, **kw: None,
})()
_log("wsgiref.simple_server.make_server stubbed ✓")

# ── 3. Import main ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
_log("Importing main module (heavy — APScheduler threads will start) …")
_t0 = time.time()
try:
    import main as _main
except SystemExit as e:
    _log(f"main import raised SystemExit({e})")
    _write_result({"verdict": "FAIL", "reason": f"SystemExit({e}) during import"})
    os._exit(1)
except Exception as e:
    _log(f"main import failed: {e!r}")
    _write_result({"verdict": "FAIL", "reason": f"import exception: {e!r}"})
    os._exit(1)
_log(f"main imported ✓  elapsed={time.time()-_t0:.1f}s")

# ── 4. Patch _g0_read_config to force ENFORCE+PAUSED ─────────────────────────
import aiem_diagram3_governance as _d3g
def _patched_g0_read_config(force=False):
    return {"mode": "ENFORCE", "state": "PAUSED", "error": None, "ts": time.time()}
_d3g._g0_read_config = _patched_g0_read_config
_log("_g0_read_config patched → ENFORCE/PAUSED ✓")

# Weekend guard: _aiem_paper_execute_today checks _is_trading_day(_today) at
# the very top, before G0 is called, and returns None on weekends.  Patch it
# to return True so the G0 BLOCK path is reached regardless of day-of-week.
# This is equivalent to patching _g0_read_config — both are environmental
# overrides for isolated test execution.
_main._is_trading_day = lambda d: True
_log("_main._is_trading_day patched → always True (bypass weekend/holiday guard) ✓")

# ── 5. Capture pre-test counts (via CLEAN URL without search_path options) ────
import psycopg2, urllib.parse as _up

_p = _up.urlparse(_db_url)
_qs = dict(_up.parse_qsl(_p.query, keep_blank_values=True))
_qs.pop("options", None)
_CLEAN_URL = _p._replace(query=_up.urlencode(_qs, quote_via=_up.quote)).geturl()

def _count(schema, table):
    conn = psycopg2.connect(_CLEAN_URL, connect_timeout=5)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    return n

def _snapshot(label):
    tables = [
        ("d3_test_isolation", "d3_governance_requests"),
        ("d3_test_isolation", "d3_governance_event_links"),
        ("d3_test_isolation", "d3_governance_decisions"),
        ("d3_test_isolation", "d3_governance_acks"),
        ("public",            "d3_governance_requests"),
        ("public",            "d3_governance_event_links"),
        ("public",            "d3_governance_decisions"),
        ("public",            "d3_governance_acks"),
        ("public",            "aiem_paper_trades"),
        ("public",            "aiem_paper_execution_log"),
    ]
    snap = {}
    for sch, tbl in tables:
        k = f"{sch}.{tbl}"
        snap[k] = _count(sch, tbl)
        _log(f"[{label}] {k} = {snap[k]}")
    return snap

pre = _snapshot("PRE")

# ── 6. Call _aiem_paper_execute_today ─────────────────────────────────────────
_log("Calling _aiem_paper_execute_today(trigger_source='directive5_fulltest') …")
_t1 = time.time()
try:
    result = _main._aiem_paper_execute_today(trigger_source="directive5_fulltest")
except Exception as exc:
    result = {"exception": str(exc)}
_log(f"Call returned in {time.time()-_t1:.3f}s: {result}")

# ── 7. Post-test counts ───────────────────────────────────────────────────────
post = _snapshot("POST")

# ── 8. Verdict ────────────────────────────────────────────────────────────────
all_pass = True
checks = []

for key, expect in [
    ("d3_test_isolation.d3_governance_requests",    ">=1"),
    ("d3_test_isolation.d3_governance_event_links", ">=1"),
    ("d3_test_isolation.d3_governance_decisions",   ">=1"),
    ("public.d3_governance_requests",               "==0"),
    ("public.d3_governance_event_links",            "==0"),
    ("public.d3_governance_decisions",              "==0"),
    ("public.d3_governance_acks",                   "==0"),
    ("public.aiem_paper_trades",                    "==0"),
]:
    delta = post[key] - pre[key]
    ok = (delta >= 1 if expect == ">=1" else delta == 0)
    if not ok:
        all_pass = False
    checks.append({"table": key, "pre": pre[key], "post": post[key],
                   "delta": delta, "expect": expect, "ok": ok})
    _log(f"[DELTA] {key}: {'+' if delta>0 else ''}{delta}  [{expect}] → {'OK' if ok else 'FAIL'}")

blocked_ok = isinstance(result, dict) and result.get("blocked") is True
if not blocked_ok:
    all_pass = False
_log(f"[DELTA] result.blocked = {result.get('blocked') if isinstance(result,dict) else result}  → {'OK' if blocked_ok else 'FAIL'}")

verdict = "PASS" if all_pass else "FAIL"
_log(f"[VERDICT] {verdict}")

result_doc = {
    "verdict": verdict,
    "timestamp_utc": _ts(),
    "call_result": str(result),
    "blocked_ok": blocked_ok,
    "checks": checks,
    "pre": pre,
    "post": post,
}
_write_result(result_doc)
_log(f"Result written to {RESULT_FILE}")

# os._exit bypasses Python shutdown/threading — terminates immediately.
os._exit(0 if all_pass else 1)
