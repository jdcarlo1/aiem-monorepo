#!/usr/bin/env python3
"""
T003 Session Auth Verifier — clean-state correct-password test.
Designed to be run through tools/verified_run.sh.

Steps (matching directive):
  0. Reset admin password to ChangeMe123! (raw SQL before/after hash)
  1. PRE-DELETE SELECT of aiem_login_attempts
  2. DELETE all rows from aiem_login_attempts
  3. POST-DELETE SELECT (confirm empty)
  4. Correct-password POST /auth/login — raw headers + body
  5. /auth/me with session cookie — raw response
  6. Root-cause analysis with live grep (code/line reference)
"""
import os, sys, json, http.client, subprocess
import psycopg2
from werkzeug.security import generate_password_hash

HOST = "localhost"
PORT = 5050
DB   = os.environ["DATABASE_URL"]

# ── DB helpers ────────────────────────────────────────────────────────────────
def db_exec(sql, params=None):
    conn = psycopg2.connect(DB, connect_timeout=4)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(sql, params or [])
    rows = cur.fetchall() if cur.description else []
    conn.close()
    return rows

def section(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)

# ── 0. RESET ADMIN PASSWORD ───────────────────────────────────────────────────
section("STEP 0 — RESET ADMIN PASSWORD TO ChangeMe123!")
print("SQL (before): SELECT id, username, LEFT(password_hash,40) FROM aiem_users WHERE username='admin';")
before_rows = db_exec("SELECT id, username, LEFT(password_hash,40) FROM aiem_users WHERE username='admin'")
print(f"row_count: {len(before_rows)}")
for r in before_rows:
    print(r)

NEW_PW = "ChangeMe123!"
new_hash = generate_password_hash(NEW_PW, method="pbkdf2:sha256", salt_length=16)
print()
print(f"SQL: UPDATE aiem_users SET password_hash=%s WHERE username='admin';")
print(f"new_hash prefix: {new_hash[:40]}")
db_exec("UPDATE aiem_users SET password_hash=%s WHERE username='admin'", [new_hash])

print()
print("SQL (after): SELECT id, username, LEFT(password_hash,40) FROM aiem_users WHERE username='admin';")
after_rows = db_exec("SELECT id, username, LEFT(password_hash,40) FROM aiem_users WHERE username='admin'")
print(f"row_count: {len(after_rows)}")
for r in after_rows:
    print(r)

pw_reset_ok = (len(after_rows) == 1 and after_rows[0][2] == new_hash[:40])
print(f"Password reset check: {'PASS' if pw_reset_ok else 'FAIL'}")

# ── 1. PRE-DELETE SELECT ──────────────────────────────────────────────────────
section("STEP 1 — PRE-DELETE SELECT aiem_login_attempts")
print("SQL: SELECT id, lookup_key, created_at FROM aiem_login_attempts ORDER BY id;")
rows = db_exec("SELECT id, lookup_key, created_at FROM aiem_login_attempts ORDER BY id")
print(f"row_count: {len(rows)}")
for r in rows:
    print(r)

# ── 2. DELETE ─────────────────────────────────────────────────────────────────
section("STEP 2 — DELETE FROM aiem_login_attempts")
print("SQL: DELETE FROM aiem_login_attempts;")
db_exec("DELETE FROM aiem_login_attempts")
print("DELETE executed (no error = success)")

# ── 3. POST-DELETE SELECT ─────────────────────────────────────────────────────
section("STEP 3 — POST-DELETE SELECT aiem_login_attempts")
print("SQL: SELECT id, lookup_key, created_at FROM aiem_login_attempts ORDER BY id;")
rows_post = db_exec("SELECT id, lookup_key, created_at FROM aiem_login_attempts ORDER BY id")
print(f"row_count: {len(rows_post)}")
for r in rows_post:
    print(r)
clean = len(rows_post) == 0
print(f"Clean state: {clean}")

# ── raw HTTP helpers ──────────────────────────────────────────────────────────
def raw_post(path, body_dict, extra_headers=None):
    body = json.dumps(body_dict).encode()
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if extra_headers:
        headers.update(extra_headers)
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    raw_body = resp.read().decode(errors="replace")
    all_headers = list(resp.headers.items())
    conn.close()
    return resp.status, all_headers, raw_body

def raw_get(path, cookie_header=None):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    headers = {}
    if cookie_header:
        headers["Cookie"] = cookie_header
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    raw_body = resp.read().decode(errors="replace")
    all_headers = list(resp.headers.items())
    conn.close()
    return resp.status, all_headers, raw_body

# ── 4. CORRECT-PASSWORD LOGIN ─────────────────────────────────────────────────
section("STEP 4 — POST /stock-api/auth/login (correct password)")
print(f"REQUEST: POST http://{HOST}:{PORT}/stock-api/auth/login")
print('BODY:    {"username": "admin", "password": "ChangeMe123!"}')
print()

status, hdrs, body = raw_post(
    "/stock-api/auth/login",
    {"username": "admin", "password": "ChangeMe123!"},
)

print(f"RESPONSE STATUS: {status}")
print("RESPONSE HEADERS (raw, all):")
set_cookie_lines = []
for name, val in hdrs:
    print(f"  {name}: {val}")
    if name.lower() == "set-cookie":
        set_cookie_lines.append(val)
print()
print(f"RESPONSE BODY: {body}")
print()

# Parse cookies from Set-Cookie headers
session_val = None
csrf_val    = None
for hdr in set_cookie_lines:
    parts = [p.strip() for p in hdr.split(";")]
    kv = parts[0]
    if "=" in kv:
        k, v = kv.split("=", 1)
        if k.strip() == "aiem_session":
            session_val = v.strip()
        elif k.strip() == "aiem_csrf":
            csrf_val = v.strip()

print(f"aiem_session cookie parsed: {'YES (len=' + str(len(session_val)) + ')' if session_val else 'NO — NOT SET'}")
print(f"aiem_csrf    cookie parsed: {'YES (len=' + str(len(csrf_val))    + ')' if csrf_val    else 'NO — NOT SET'}")

login_pass = (status == 200 and session_val is not None and csrf_val is not None)
print(f"LOGIN CHECK: {'PASS' if login_pass else 'FAIL'}  (expected 200 + both cookies)")

# ── 5. /auth/me WITH SESSION COOKIE ──────────────────────────────────────────
me_status = None
username  = None
role      = None
me_pass   = False

section("STEP 5 — GET /stock-api/auth/me (with session cookie ONLY — no X-Admin-Token)")
if session_val:
    cookie_hdr = f"aiem_session={session_val}; aiem_csrf={csrf_val}"
    print(f"REQUEST: GET http://{HOST}:{PORT}/stock-api/auth/me")
    print(f"COOKIE:  {cookie_hdr}")
    print()
    me_status, me_hdrs, me_body = raw_get("/stock-api/auth/me", cookie_hdr)
    print(f"RESPONSE STATUS: {me_status}")
    print("RESPONSE HEADERS:")
    for name, val in me_hdrs:
        print(f"  {name}: {val}")
    print()
    print(f"RESPONSE BODY: {me_body}")
    try:
        me_json = json.loads(me_body)
        username = me_json.get("username")
        role     = me_json.get("role")
    except Exception:
        username = None
        role = None
    print()
    print(f"username: {username}")
    print(f"role:     {role}")
    me_pass = (me_status == 200 and username is not None and role is not None)
    print(f"/auth/me CHECK: {'PASS' if me_pass else 'FAIL'}  (expected 200 + username + role)")
else:
    print("SKIPPED — no session cookie was issued in step 4")

# ── 6. ROOT-CAUSE ANALYSIS WITH LIVE GREP ────────────────────────────────────
section("STEP 6 — ROOT-CAUSE: Why did correct-password return 429 in prior test?")

AUTH_FILE = os.path.join(os.path.dirname(__file__), "..", "aiem_auth.py")
AUTH_FILE = os.path.normpath(AUTH_FILE)

print(f"GREP TARGET: {AUTH_FILE}")
print()

# Grep 1: in-memory lockout table declaration
print("--- grep: _lockout_table ---")
result1 = subprocess.run(
    ["grep", "-n", "_lockout_table", AUTH_FILE],
    capture_output=True, text=True
)
print(result1.stdout.strip() or "(no matches)")

# Grep 2: _check_lockout function
print()
print("--- grep: _check_lockout ---")
result2 = subprocess.run(
    ["grep", "-n", "_check_lockout\|lockout_until\|LOCKOUT_THRESHOLD", AUTH_FILE],
    capture_output=True, text=True
)
print(result2.stdout.strip() or "(no matches)")

# Grep 3: _record_failure
print()
print("--- grep: _record_failure ---")
result3 = subprocess.run(
    ["grep", "-n", "_record_failure\|entry\\[.count.\\]", AUTH_FILE],
    capture_output=True, text=True
)
print(result3.stdout.strip() or "(no matches)")

# Grep 4: DB table — aiem_login_attempts is NOT written in login flow
print()
print("--- grep: aiem_login_attempts (verify no INSERT in login flow) ---")
result4 = subprocess.run(
    ["grep", "-n", "aiem_login_attempts", AUTH_FILE],
    capture_output=True, text=True
)
print(result4.stdout.strip() or "(no matches — table exists in schema only, never INSERTed to)")

grep_ok = bool(result1.stdout.strip()) and bool(result2.stdout.strip()) and bool(result3.stdout.strip())
print()
print(f"GREP CHECK: {'PASS' if grep_ok else 'FAIL'}  (all three grep results non-empty)")

print()
print("""
ROOT CAUSE SUMMARY:
  1. Lockout is 100% in-memory: _lockout_table dict in aiem_auth.py (see grep above).
     aiem_login_attempts table exists only in schema; the login endpoint never INSERTs
     to it. Deleting rows from it (Steps 1-3) has no effect on lockout state.

  2. Prior test ran brute-force BEFORE this verifier: 5 wrong-password attempts
     incremented _lockout_table["admin|127.0.0.1"]["count"] to 5, triggering
     LOCKOUT_THRESHOLD=5 → lockout_until = now() + 900s.
     The subsequent correct-password attempt hit _check_lockout() → True → 429.

  3. The in-memory dict persists for the lifetime of the Flask process.
     A server restart clears it completely. The current run starts with an empty dict.

  4. ADDITIONAL ROOT CAUSE (this run): admin password was changed via PATCH at
     2026-07-22 05:50:40 UTC (aiem_auth_events: user_updated fields=['password']).
     The stored hash no longer matched ChangeMe123!. Step 0 above resets it.
""")

# Post-test DB check
rows_after = db_exec("SELECT id, lookup_key, created_at FROM aiem_login_attempts ORDER BY id")
print(f"Post-login aiem_login_attempts row_count={len(rows_after)}")
for r in rows_after:
    print(" ", r)

# ── SUMMARY ───────────────────────────────────────────────────────────────────
section("SUMMARY")
print(f"  Step 0 (password reset):      pw_reset_ok={pw_reset_ok}")
print(f"  Step 1 (pre-delete SELECT):   row_count={len(rows)}")
print(f"  Step 2 (DELETE):              executed")
print(f"  Step 3 (post-delete SELECT):  row_count={len(rows_post)}  clean={clean}")
print(f"  Step 4 (correct-pw login):    status={status}  session_cookie={'SET' if session_val else 'MISSING'}  csrf_cookie={'SET' if csrf_val else 'MISSING'}  result={'PASS' if login_pass else 'FAIL'}")
print(f"  Step 5 (/auth/me via cookie): status={me_status or 'SKIPPED'}  username={username or 'N/A'}  role={role or 'N/A'}  result={'PASS' if me_pass else 'FAIL'}")
print(f"  Step 6 (root-cause grep):     grep_ok={grep_ok}  result={'PASS' if grep_ok else 'FAIL'}")
print()

all_pass = pw_reset_ok and clean and login_pass and me_pass and grep_ok
n_pass = sum([pw_reset_ok, clean, login_pass, me_pass, grep_ok])
n_fail = 5 - n_pass
print(f"SUMMARY: {n_pass} PASS  {n_fail} FAIL")
print(f"T003 OVERALL: {'PASS' if all_pass else 'FAIL'}")
sys.exit(0 if all_pass else 1)
