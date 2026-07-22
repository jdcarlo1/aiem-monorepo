#!/usr/bin/env python3
"""
T003 Follow-Up Item 1 — Post-rotation verification.
Rotation already executed at verified_run.sh entry #74 (SEQ=69).
This script provides the clean PASS evidence with corrected hash check.

Hash-changed proof: old_pw->401 AND new_pw->200 is the functional proof.
Additionally shows sha256 fingerprint of current stored hash (proves it is
a real pbkdf2 hash, not a placeholder or empty string).

Reads new password from /tmp/aiem_new_admin_pw.txt (written during rotation).
"""
import os, sys, json, http.client, hashlib, psycopg2

HOST    = "localhost"
PORT    = 5050
DB      = os.environ["DATABASE_URL"]
OLD_PW  = "ChangeMe123!"
PW_FILE = "/tmp/aiem_new_admin_pw.txt"

if not os.path.exists(PW_FILE):
    print(f"FAIL: {PW_FILE} not found — cannot read new password for test")
    sys.exit(1)

with open(PW_FILE) as f:
    new_pw = f.read().strip()

pw_mask = new_pw[:4] + ("*" * (len(new_pw) - 4))

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

def raw_post(path, body_dict):
    body = json.dumps(body_dict).encode()
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    conn.request("POST", path, body=body, headers={
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    })
    resp = conn.getresponse()
    raw = resp.read().decode(errors="replace")
    hdrs = list(resp.headers.items())
    conn.close()
    return resp.status, hdrs, raw

# ── CONTEXT ───────────────────────────────────────────────────────────────────
section("CONTEXT")
print("Password rotation executed at verified_run.sh entry #74 (DPL-chain SEQ=69).")
print("That run confirmed: PATCH 200, old_pw->401 PASS, new_pw->200 PASS.")
print("The FAIL at SEQ=69 was a false negative: hash_changed compared 14-char")
print("prefix 'pbkdf2:sha256:' which is identical for ALL pbkdf2 hashes.")
print("This script provides corrected evidence with full hash fingerprint.")

# ── 1. CURRENT DB HASH FINGERPRINT ───────────────────────────────────────────
section("STEP 1 — Current DB hash state")
print("SQL: SELECT id, username, role, is_active, LEFT(password_hash,14), LENGTH(password_hash) FROM aiem_users WHERE username='admin'")
rows = db_exec("SELECT id, username, role, is_active, LEFT(password_hash,14), LENGTH(password_hash) FROM aiem_users WHERE username='admin'")
print(f"row_count: {len(rows)}")
for r in rows: print(r)

full_rows = db_exec("SELECT password_hash FROM aiem_users WHERE username='admin'")
stored_hash = full_rows[0][0] if full_rows else ""
fingerprint = hashlib.sha256(stored_hash.encode()).hexdigest()
print(f"\nStored hash sha256 fingerprint (of full pbkdf2 string):")
print(f"  {fingerprint}")
print(f"Hash algorithm prefix: {stored_hash[:14]}")
print(f"Hash length:           {len(stored_hash)} chars")
db_ok = (len(stored_hash) > 50 and stored_hash.startswith("pbkdf2:sha256:"))
print(f"DB hash valid:         {db_ok}")

# ── 2. OLD PASSWORD -> 401 ────────────────────────────────────────────────────
section("STEP 2 — POST /auth/login with OLD password 'ChangeMe123!' (expect 401)")
print(f'REQUEST: POST http://{HOST}:{PORT}/stock-api/auth/login')
print('BODY:    {"username": "admin", "password": "ChangeMe123!"}')
old_status, old_hdrs, old_body = raw_post("/stock-api/auth/login",
    {"username": "admin", "password": OLD_PW})
print(f"RESPONSE STATUS: {old_status}")
print(f"RESPONSE BODY:   {old_body}")
old_pass = (old_status == 401)
print(f"OLD PASSWORD CHECK: {'PASS' if old_pass else 'FAIL'}  (expected 401, got {old_status})")

# ── 3. NEW PASSWORD -> 200 ────────────────────────────────────────────────────
section("STEP 3 — POST /auth/login with NEW password (expect 200)")
print(f'REQUEST: POST http://{HOST}:{PORT}/stock-api/auth/login')
print(f'BODY:    {{"username": "admin", "password": "{pw_mask}"}}  [full value in {PW_FILE}]')
new_status, new_hdrs, new_body = raw_post("/stock-api/auth/login",
    {"username": "admin", "password": new_pw})
print(f"RESPONSE STATUS: {new_status}")
print(f"RESPONSE BODY:   {new_body}")
for n, v in new_hdrs:
    if n.lower() == "set-cookie":
        print(f"  {n}: {v}")
new_pass = (new_status == 200)
print(f"NEW PASSWORD CHECK: {'PASS' if new_pass else 'FAIL'}  (expected 200, got {new_status})")

# ── 4. HASH CHANGE PROOF ──────────────────────────────────────────────────────
section("STEP 4 — Hash-change proof (functional)")
print("Direct bit comparison of pre/post stored hash is unavailable (pre-rotation")
print("state is gone from DB — correct, rotation is a one-way write).")
print()
print("Functional proof is: old_pw->401 AND new_pw->200.")
print("If the hash had NOT changed, both passwords would behave as before:")
print("  - old_pw would still return 200 (it returned 401 -> hash changed)")
print("  - new_pw would return 401 (it returned 200 -> new hash works)")
print()
print("Additionally, at SEQ=69 entry #74, the PATCH response was:")
print("  HTTP 200  {'status': 'updated', 'user_id': 1}")
print("  This is the server confirming the UPDATE was applied.")
hash_proof_pass = (old_pass and new_pass)
print(f"Hash-change functional proof: {'PASS' if hash_proof_pass else 'FAIL'}")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
section("SUMMARY")
checks = {
    "db_hash_is_valid_pbkdf2": db_ok,
    "old_pw_returns_401":      old_pass,
    "new_pw_returns_200":      new_pass,
    "hash_change_functional":  hash_proof_pass,
}
for label, result in checks.items():
    print(f"  {label:35s}: {'PASS' if result else 'FAIL'}")

n_pass = sum(checks.values())
n_fail = len(checks) - n_pass
print()
print(f"  New password file: {PW_FILE}")
print(f"  Action required:   Store this password in AIEM_DEFAULT_ADMIN_PW secret")
print()
print(f"SUMMARY: {n_pass} PASS {n_fail} FAIL")
print(f"T003-PW-VERIFY OVERALL: {'PASS' if all(checks.values()) else 'FAIL'}")
sys.exit(0 if all(checks.values()) else 1)
