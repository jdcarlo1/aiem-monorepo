#!/usr/bin/env python3
"""
T003 Follow-Up Item 1 — Admin password rotation verifier.
Run through tools/verified_run.sh.

Uses the existing PATCH /stock-api/auth/users/1 endpoint — no bcrypt import needed.
New password is written to /tmp/aiem_new_admin_pw.txt (mode 600) and NOT included
in stdout so it is not captured in evidence_chain.log.

Steps:
  1. PRE-STATE: SELECT admin row (id, role, is_active, hash prefix — no plaintext)
  2. Login with OLD password -> get session + CSRF token
  3. PATCH /auth/users/1 with new password (plaintext masked in log)
  4. POST-STATE: SELECT admin row (hash prefix must differ)
  5. HTTP test: POST /auth/login with OLD password -> expect 401
  6. HTTP test: POST /auth/login with NEW password -> expect 200
"""
import os, sys, json, http.client, secrets, psycopg2

HOST = "localhost"
PORT = 5050
DB   = os.environ["DATABASE_URL"]
OLD_PW   = "ChangeMe123!"
PW_FILE  = "/tmp/aiem_new_admin_pw.txt"

def db_exec(sql, params=None):
    conn = psycopg2.connect(DB, connect_timeout=4)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(sql, params or [])
    rows = cur.fetchall() if cur.description else []
    conn.close()
    return rows

def section(title):
    print(); print("=" * 70); print(title); print("=" * 70)

class RawHTTP:
    """Thin HTTP client that exposes raw response headers."""
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self._session = None
        self._csrf    = None

    def _conn(self):
        return http.client.HTTPConnection(self.host, self.port, timeout=12)

    def post(self, path, body_dict, extra_headers=None):
        body = json.dumps(body_dict).encode()
        hdrs = {"Content-Type": "application/json",
                "Content-Length": str(len(body))}
        if extra_headers:
            hdrs.update(extra_headers)
        c = self._conn()
        c.request("POST", path, body=body, headers=hdrs)
        r = c.getresponse()
        raw = r.read().decode(errors="replace")
        all_hdrs = list(r.headers.items())
        c.close()
        return r.status, all_hdrs, raw

    def patch(self, path, body_dict):
        if not self._session or not self._csrf:
            raise RuntimeError("must login before PATCH")
        body = json.dumps(body_dict).encode()
        hdrs = {"Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Cookie": f"aiem_session={self._session}; aiem_csrf={self._csrf}",
                "X-CSRF-Token": self._csrf}
        c = self._conn()
        c.request("PATCH", path, body=body, headers=hdrs)
        r = c.getresponse()
        raw = r.read().decode(errors="replace")
        c.close()
        return r.status, raw

    def store_cookies(self, headers):
        for name, val in headers:
            if name.lower() == "set-cookie":
                parts = [p.strip() for p in val.split(";")]
                kv = parts[0]
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if k.strip() == "aiem_session":
                        self._session = v.strip()
                    elif k.strip() == "aiem_csrf":
                        self._csrf    = v.strip()

http_client = RawHTTP(HOST, PORT)

# ── GENERATE NEW PASSWORD (write to file, NOT stdout) ────────────────────────
new_pw = secrets.token_urlsafe(24)
with open(PW_FILE, "w") as f:
    f.write(new_pw + "\n")
os.chmod(PW_FILE, 0o600)
# Mask for log: show length and first 4 chars only
pw_mask = new_pw[:4] + ("*" * (len(new_pw) - 4))

# ── 1. PRE-STATE ─────────────────────────────────────────────────────────────
section("STEP 1 — PRE-STATE: aiem_users admin row (hash prefix, no plaintext)")
sql_prefix = "SELECT id, username, role, is_active, LEFT(password_hash, 14) FROM aiem_users WHERE username='admin'"
sql_full   = "SELECT password_hash FROM aiem_users WHERE username='admin'"
print(f"SQL: {sql_prefix}")
rows = db_exec(sql_prefix)
print(f"row_count: {len(rows)}")
for r in rows: print(r)
pre_full_hash = (db_exec(sql_full)[0][0] if rows else "")
print(f"pre-change hash prefix (14 chars): {rows[0][4] if rows else None}")
print(f"pre-change hash sha256: {__import__('hashlib').sha256(pre_full_hash.encode()).hexdigest()[:16]}...")

# ── 2. LOGIN WITH OLD PASSWORD ────────────────────────────────────────────────
section("STEP 2 — POST /auth/login with OLD password to obtain session+CSRF")
print(f'REQUEST: POST http://{HOST}:{PORT}/stock-api/auth/login')
print('BODY:    {"username": "admin", "password": "ChangeMe123!"}')
status, hdrs, body = http_client.post(
    "/stock-api/auth/login",
    {"username": "admin", "password": OLD_PW},
)
http_client.store_cookies(hdrs)
print(f"RESPONSE STATUS: {status}")
print(f"RESPONSE BODY:   {body}")
for n, v in hdrs:
    if n.lower() == "set-cookie":
        print(f"  Set-Cookie: {v}")
login_pre_pass = (status == 200 and http_client._session is not None)
print(f"LOGIN (pre-rotate): {'PASS' if login_pre_pass else 'FAIL'}")

# ── 3. PATCH NEW PASSWORD ─────────────────────────────────────────────────────
section("STEP 3 — PATCH /auth/users/1 (change password, value masked in log)")
print(f'REQUEST: PATCH http://{HOST}:{PORT}/stock-api/auth/users/1')
print(f'BODY:    {{"password": "{pw_mask}"}}  [full value in {PW_FILE}]')
patch_status, patch_body = http_client.patch(
    "/stock-api/auth/users/1",
    {"password": new_pw},          # actual value sent, not printed above
)
print(f"RESPONSE STATUS: {patch_status}")
print(f"RESPONSE BODY:   {patch_body}")
patch_pass = (patch_status == 200)
print(f"PATCH CHECK: {'PASS' if patch_pass else 'FAIL'}  (expected 200)")

# ── 4. POST-STATE ─────────────────────────────────────────────────────────────
section("STEP 4 — POST-STATE: confirm hash changed in DB")
import hashlib
rows_post = db_exec(sql_prefix)
print(f"SQL: {sql_prefix}")
for r in rows_post: print(r)
post_full_hash = (db_exec(sql_full)[0][0] if rows_post else "")
post_fingerprint = hashlib.sha256(post_full_hash.encode()).hexdigest()[:16]
pre_fingerprint  = hashlib.sha256(pre_full_hash.encode()).hexdigest()[:16]
print(f"pre-change  hash fingerprint (sha256[:16] of stored value): {pre_fingerprint}...")
print(f"post-change hash fingerprint (sha256[:16] of stored value): {post_fingerprint}...")
hash_changed = (pre_full_hash != post_full_hash)
print(f"hash changed (full string comparison): {hash_changed}")

# ── 5. OLD PASSWORD -> 401 ────────────────────────────────────────────────────
section("STEP 5 — POST /auth/login with OLD password (expect 401)")
print(f'REQUEST: POST http://{HOST}:{PORT}/stock-api/auth/login')
print('BODY:    {"username": "admin", "password": "ChangeMe123!"}')
old_status, old_hdrs, old_body = http_client.post(
    "/stock-api/auth/login",
    {"username": "admin", "password": OLD_PW},
)
print(f"RESPONSE STATUS: {old_status}")
print(f"RESPONSE BODY:   {old_body}")
old_pass = (old_status == 401)
print(f"OLD PASSWORD CHECK: {'PASS' if old_pass else 'FAIL'}  (expected 401, got {old_status})")

# ── 6. NEW PASSWORD -> 200 ────────────────────────────────────────────────────
section("STEP 6 — POST /auth/login with NEW password (expect 200)")
print(f'REQUEST: POST http://{HOST}:{PORT}/stock-api/auth/login')
print(f'BODY:    {{"username": "admin", "password": "{pw_mask}"}}  [full value in {PW_FILE}]')
new_status, new_hdrs, new_body = http_client.post(
    "/stock-api/auth/login",
    {"username": "admin", "password": new_pw},
)
print(f"RESPONSE STATUS: {new_status}")
print(f"RESPONSE BODY:   {new_body}")
for n, v in new_hdrs:
    if n.lower() == "set-cookie":
        print(f"  Set-Cookie: {v}")
new_pass = (new_status == 200)
print(f"NEW PASSWORD CHECK: {'PASS' if new_pass else 'FAIL'}  (expected 200, got {new_status})")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
section("SUMMARY")
checks = {
    "login_pre_rotate":   login_pre_pass,
    "patch_200":          patch_pass,
    "hash_changed_in_db": hash_changed,
    "old_pw_returns_401": old_pass,
    "new_pw_returns_200": new_pass,
}
for label, result in checks.items():
    print(f"  {label:30s}: {'PASS' if result else 'FAIL'}")
n_pass = sum(checks.values())
n_fail = len(checks) - n_pass
print()
print(f"  New password written to: {PW_FILE}  (chmod 600, not in this log)")
print(f"  Mask shown in log:        {pw_mask}")
print(f"  Action required:          cat {PW_FILE} → store in AIEM_DEFAULT_ADMIN_PW secret")
print()
print(f"SUMMARY: {n_pass} PASS {n_fail} FAIL")
print(f"T003-PW-ROTATE OVERALL: {'PASS' if all(checks.values()) else 'FAIL'}")
sys.exit(0 if all(checks.values()) else 1)
