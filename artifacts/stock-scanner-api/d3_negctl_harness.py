"""
d3_negctl_harness.py
====================
Directive 5 — Option 2 isolation harness for the D3 negative-control test.

Isolation mechanism (NOT a monkey-patch):
  os.environ['DATABASE_URL'] is rewritten at the top of this script, before
  any import of aiem_diagram3_governance, to append
  `options=-c search_path=d3_test_isolation,public`.
  Because _d3_connect() reads os.environ["DATABASE_URL"] at call time (not at
  import time), every psycopg2 connection opened by any D3 function during this
  script's lifetime will use the test schema's search_path.  All unqualified
  table references (d3_governance_requests, d3_governance_event_links, etc.)
  resolve to d3_test_isolation first, then public.  Production tables in the
  public schema are never touched.

Condition-forcing mechanism (monkey-patch, approved for condition-forcing only):
  _g0_read_config() is patched to return (mode=ENFORCE, state=PAUSED) so the
  G0 checkpoint reliably emits a BLOCK decision regardless of the live DB row.
  This is the same mechanism used in the original Directive B test — the
  isolation mechanism is what changed (schema isolation, not a rollback).

Usage:
  python3 d3_negctl_harness.py [--dry-run | --full-test]

  --dry-run   : Call require_governance_authorization once, verify writes went
                to d3_test_isolation and not public.  No _aiem_paper_execute_today.
  --full-test : Call the actual _aiem_paper_execute_today BLOCK path through
                the full G0 gate, verifying the paper-trade write is blocked and
                all G0 audit writes landed in d3_test_isolation.
"""

import os
import sys
import urllib.parse

# ── 1. REWRITE DATABASE_URL BEFORE ANY D3 IMPORT ─────────────────────────────

_ORIG_DB_URL = os.environ.get("DATABASE_URL", "")
if not _ORIG_DB_URL:
    print("FATAL: DATABASE_URL not set")
    sys.exit(1)

# libpq URI format uses percent-decoding (%XX), NOT plus-decoding (+ → space).
# urllib.parse.urlencode defaults to HTML form encoding (spaces → +), which
# causes libpq to see the literal parameter name "+search_path".
# Fix: quote_via=urllib.parse.quote forces %20 for spaces (RFC 3986).
_parsed = urllib.parse.urlparse(_ORIG_DB_URL)
_qs_params = dict(urllib.parse.parse_qsl(_parsed.query, keep_blank_values=True))
_qs_params["options"] = "-c search_path=d3_test_isolation,public"
_new_query = urllib.parse.urlencode(_qs_params, quote_via=urllib.parse.quote)
_TEST_DB_URL = _parsed._replace(query=_new_query).geturl()
os.environ["DATABASE_URL"] = _TEST_DB_URL

print(f"[ISOLATION] DATABASE_URL rewritten: search_path=d3_test_isolation,public injected")
print(f"[ISOLATION] options fragment: {_qs_params['options']}")

# Probe: open one connection with the test URL and verify search_path
try:
    _probe_conn = __import__("psycopg2").connect(_TEST_DB_URL, connect_timeout=5)
    _probe_cur = _probe_conn.cursor()
    _probe_cur.execute("SHOW search_path")
    _sp = _probe_cur.fetchone()[0]
    _probe_cur.execute("SELECT current_schema()")
    _cs = _probe_cur.fetchone()[0]
    _probe_cur.close()
    _probe_conn.close()
    print(f"[ISOLATION] Probe connection search_path='{_sp}', current_schema='{_cs}'")
    if "d3_test_isolation" not in _sp:
        print("[ISOLATION] WARN: d3_test_isolation not in search_path — URL injection may not have worked")
except Exception as _pe:
    print(f"[ISOLATION] Probe connection failed: {_pe}")

# ── 2. IMPORTS (happen after DATABASE_URL is set) ─────────────────────────────

sys.path.insert(0, os.path.dirname(__file__))

import psycopg2
import aiem_diagram3_governance as _d3g

# ── 3. CONDITION-FORCING: monkey-patch _g0_read_config ───────────────────────
# Returns (mode=ENFORCE, state=PAUSED) so G0 always produces BLOCK.
# Approved for condition-forcing; isolation is handled by DATABASE_URL above.

import time as _time

def _patched_g0_read_config(force=False):
    """Return the dict shape that _evaluate_g0_decision expects:
       keys mode, state, error, ts.  Forces ENFORCE+PAUSED so G0 always
       emits BLOCK for the negative-control test."""
    return {"mode": "ENFORCE", "state": "PAUSED", "error": None, "ts": _time.time()}

_d3g._g0_read_config = _patched_g0_read_config
print("[CONDITION] _g0_read_config patched → {mode: ENFORCE, state: PAUSED}")

# ── 4. HELPERS ────────────────────────────────────────────────────────────────

def _snapshot_counts(label):
    """Return row counts from both schemas for the four key audit tables."""
    conn = psycopg2.connect(_ORIG_DB_URL)  # use ORIGINAL URL to read both schemas
    cur = conn.cursor()
    rows = {}
    for schema in ("d3_test_isolation", "public"):
        cur.execute(f"""
            SELECT 'requests',   COUNT(*) FROM {schema}.d3_governance_requests
            UNION ALL
            SELECT 'event_links',COUNT(*) FROM {schema}.d3_governance_event_links
            UNION ALL
            SELECT 'decisions',  COUNT(*) FROM {schema}.d3_governance_decisions
            UNION ALL
            SELECT 'acks',       COUNT(*) FROM {schema}.d3_governance_acks
        """)
        for tbl, cnt in cur.fetchall():
            rows[(schema, tbl)] = cnt
    cur.close()
    conn.close()
    print(f"\n[COUNTS — {label}]")
    for schema in ("d3_test_isolation", "public"):
        print(f"  {schema}:")
        for tbl in ("requests", "event_links", "decisions", "acks"):
            print(f"    {tbl:12s} = {rows[(schema, tbl)]}")
    return rows


def _fetch_newest_test_row():
    """Return the most recently inserted row from d3_test_isolation.d3_governance_requests."""
    conn = psycopg2.connect(_ORIG_DB_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT governance_request_id, trigger_source, is_test_record,
               request_timestamp_utc
        FROM d3_test_isolation.d3_governance_requests
        ORDER BY request_timestamp_utc DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


# ── 5. DRY-RUN PROOF ──────────────────────────────────────────────────────────

def run_dry_run():
    print("\n" + "=" * 70)
    print("DRY-RUN PROOF: single require_governance_authorization call")
    print("=" * 70)

    pre = _snapshot_counts("PRE-CALL")

    print("\n[CALL] require_governance_authorization(checkpoint=G0, entrypoint=_aiem_paper_execute_today, "
          "run_kind=TRADE_EXECUTING, trigger_source=directive5_dryrun)")
    try:
        result = _d3g.require_governance_authorization(
            checkpoint="G0",
            entrypoint="_aiem_paper_execute_today",
            run_kind="TRADE_EXECUTING",
            trigger_source="directive5_dryrun",
            is_test_record=False,
        )
    except Exception as exc:
        result = {"error": str(exc)}

    print(f"[RESULT] {result}")

    post = _snapshot_counts("POST-CALL")

    print("\n[DELTA]")
    all_pass = True
    for schema in ("d3_test_isolation", "public"):
        for tbl in ("requests", "event_links", "decisions", "acks"):
            delta = post[(schema, tbl)] - pre[(schema, tbl)]
            sign = "+" if delta > 0 else ""
            expected_nonzero = (schema == "d3_test_isolation")
            if delta > 0 and schema == "public":
                all_pass = False
                flag = "  ← FAIL: production write detected!"
            elif delta == 0 and schema == "d3_test_isolation" and tbl in ("requests", "event_links", "decisions"):
                all_pass = False
                flag = "  ← FAIL: expected ≥1 write here"
            else:
                flag = ""
            print(f"  {schema}.{tbl:12s}: {sign}{delta}{flag}")

    newest = _fetch_newest_test_row()
    if newest:
        print(f"\n[NEWEST TEST ROW in d3_test_isolation.d3_governance_requests]")
        print(f"  governance_request_id : {newest[0]}")
        print(f"  trigger_source        : {newest[1]}")
        print(f"  is_test_record        : {newest[2]}")
        print(f"  request_timestamp_utc : {newest[3]}")
    else:
        print("\n[WARN] No rows found in d3_test_isolation.d3_governance_requests after call")
        all_pass = False

    print(f"\n[DRY-RUN VERDICT] {'PASS — isolation confirmed' if all_pass else 'FAIL — see flags above'}")
    return all_pass


# ── 6. FULL NEGATIVE-CONTROL TEST ─────────────────────────────────────────────

def run_full_test():
    """
    Runs the full negative-control test via _aiem_paper_execute_today.

    main.py binds port 5050 at import-time (early-port-bind pattern).
    Since the stock-api workflow already holds that port, importing main
    directly in this process fails.  Solution: run
    d3_negctl_fulltest_subprocess.py as a child process, which patches
    socket.bind + wsgiref.make_server before importing main, then calls
    _aiem_paper_execute_today and reports results via stdout.  The child
    inherits os.environ (including the test DATABASE_URL) from this process.
    """
    import subprocess

    print("\n" + "=" * 70)
    print("FULL NEGATIVE-CONTROL TEST: _aiem_paper_execute_today BLOCK path")
    print("(runs in subprocess to isolate main.py early-port-bind)")
    print("=" * 70)

    sub_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "d3_negctl_fulltest_subprocess.py")
    print(f"[SUBPROCESS] launching {sub_script}")
    print(f"[SUBPROCESS] DATABASE_URL has d3_test_isolation search_path ✓")
    print()

    try:
        proc = subprocess.run(
            [sys.executable, sub_script],
            capture_output=True,
            text=True,
            timeout=600,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        print("[SUBPROCESS] TIMEOUT after 180s")
        return False
    except Exception as exc:
        print(f"[SUBPROCESS] failed to launch: {exc!r}")
        return False

    print(proc.stdout)
    if proc.stderr:
        print("[SUBPROCESS STDERR (first 3000 chars)]")
        print(proc.stderr[:3000])

    ok = proc.returncode == 0
    print(f"\n[FULL-TEST VERDICT] {'PASS — subprocess exit 0' if ok else 'FAIL — subprocess exit ' + str(proc.returncode)}")
    return ok


# ── 7. ENTRY POINT ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--dry-run"
    if mode == "--dry-run":
        ok = run_dry_run()
    elif mode == "--full-test":
        ok = run_full_test()
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(2)
    sys.exit(0 if ok else 1)
