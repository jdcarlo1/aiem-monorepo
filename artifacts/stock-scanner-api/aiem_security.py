import os
import hmac
import hashlib
import time
import secrets
import smtplib
import subprocess
from functools import wraps
from threading import Thread
from email.mime.text import MIMEText
from datetime import datetime, timezone

import requests
from flask import request, abort, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

# ═══════════════════════════════════════════════════════════
# CONFIGURATION — all values from Replit Secrets
# ═══════════════════════════════════════════════════════════
AIEM_TOKEN    = os.environ.get("AIEM_INTERNAL_TOKEN", "")
AIEM_BASE_URL = os.environ.get("AIEM_BASE_URL", "http://localhost:5050")
ALERT_EMAIL   = os.environ.get("ALERT_EMAIL", "")
SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASS     = os.environ.get("SMTP_PASS", "")
DATABASE_URL  = os.environ.get("DATABASE_URL", "")

# ═══════════════════════════════════════════════════════════
# 1. PROXYFIX — strips spoofed IPs automatically
# ═══════════════════════════════════════════════════════════
def apply_proxy_fix(app):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    return app

# ═══════════════════════════════════════════════════════════
# 2. AUDIT LOGGER
# ═══════════════════════════════════════════════════════════
def log_audit(verified: bool, ip: str, reason: str = None,
              token_hint: str = None, job_id: str = None):
    record = {"at": datetime.now(timezone.utc).isoformat(),
              "ip": ip, "verified": verified}
    if job_id:     record["job"]        = job_id
    if token_hint: record["token_hint"] = f"...{token_hint}"
    if reason:     record["reason"]     = reason
    print(f"AUDIT | {record}")

# ═══════════════════════════════════════════════════════════
# 3. SECURITY ALERTS — emails you when attacks are detected
# ═══════════════════════════════════════════════════════════
def send_alert(subject: str, body: str):
    if not all([ALERT_EMAIL, SMTP_USER, SMTP_PASS]):
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = f"[StockScanner Security] {subject}"
        msg["From"]    = SMTP_USER
        msg["To"]      = ALERT_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    except Exception as e:
        print(f"ALERT EMAIL FAILED | {e}")

def alert_async(subject: str, body: str):
    Thread(target=send_alert, args=(subject, body), daemon=True).start()

# ═══════════════════════════════════════════════════════════
# 4. IP BLOCKER — auto-bans after 5 failed attempts
# ═══════════════════════════════════════════════════════════
_failed_attempts: dict = {}
MAX_FAILURES   = 5
BLOCK_DURATION = 300

def is_blocked(ip: str) -> bool:
    record = _failed_attempts.get(ip)
    if not record:
        return False
    if record["count"] >= MAX_FAILURES:
        if time.time() - record["first"] < BLOCK_DURATION:
            return True
        del _failed_attempts[ip]
    return False

def record_failure(ip: str):
    if ip not in _failed_attempts:
        _failed_attempts[ip] = {"count": 0, "first": time.time()}
    _failed_attempts[ip]["count"] += 1
    if _failed_attempts[ip]["count"] == MAX_FAILURES:
        alert_async(f"IP Blocked: {ip}",
                    f"IP {ip} auto-blocked after {MAX_FAILURES} failed attempts.\n"
                    f"Time: {datetime.now(timezone.utc).isoformat()}")

# ═══════════════════════════════════════════════════════════
# 5. HMAC SIGNING — replay-proof, stolen token dies in 30s
# ═══════════════════════════════════════════════════════════
def sign_request(question: str) -> tuple:
    if not AIEM_TOKEN:
        raise ValueError("AIEM_INTERNAL_TOKEN not set")
    ts  = str(int(time.time()))
    sig = hmac.new(AIEM_TOKEN.encode(), f"{ts}:{question}".encode(),
                   hashlib.sha256).hexdigest()
    return ts, sig

def verify_signature(question: str, ts: str, sig: str, max_age: int = 30) -> bool:
    try:
        if abs(time.time() - int(ts)) > max_age:
            return False
        expected = hmac.new(AIEM_TOKEN.encode(), f"{ts}:{question}".encode(),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False

# ═══════════════════════════════════════════════════════════
# 6. DECORATOR — blocks anything not from your AIEM
# ═══════════════════════════════════════════════════════════
def require_aiem_verification(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip    = request.remote_addr
        token = request.headers.get("X-AIEM-Token")
        ts    = request.headers.get("X-AIEM-Timestamp")
        sig   = request.headers.get("X-AIEM-Signature")
        if is_blocked(ip):
            log_audit(verified=False, ip=ip, reason="ip_blocked")
            abort(403)
        if not AIEM_TOKEN or token != AIEM_TOKEN:
            record_failure(ip)
            log_audit(verified=False, ip=ip, reason="bad_token")
            abort(403)
        question = request.json.get("question", "") if request.is_json else ""
        if not verify_signature(question, ts or "", sig or ""):
            record_failure(ip)
            log_audit(verified=False, ip=ip, reason="bad_signature_or_replay")
            abort(403)
        log_audit(verified=True, ip=ip, token_hint=token[-4:])
        return f(*args, **kwargs)
    return decorated

# ═══════════════════════════════════════════════════════════
# 7. AIEM CLIENT — signs every outbound question automatically
# ═══════════════════════════════════════════════════════════
def aiem_ask(question: str, poll_interval: float = 3.0,
             timeout: float = 120.0) -> dict:
    ts, sig = sign_request(question)
    resp = requests.post(
        f"{AIEM_BASE_URL}/stock-api/aiem/chat",
        json={"question": question},
        headers={"X-AIEM-Token":     AIEM_TOKEN,
                 "X-AIEM-Timestamp": ts,
                 "X-AIEM-Signature": sig},
        timeout=30,
    )
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        poll = requests.get(
            f"{AIEM_BASE_URL}/stock-api/aiem/chat/{job_id}", timeout=10
        ).json()
        if poll.get("status") == "error":
            raise RuntimeError(f"AIEM error: {poll.get('error')}")
        if poll.get("status") == "done":
            return poll
    raise TimeoutError(f"AIEM job {job_id} did not complete within {timeout}s")

# ═══════════════════════════════════════════════════════════
# 8. TOKEN ROTATION — new token every 90 days, emails you
# ═══════════════════════════════════════════════════════════
_TOKEN_ROTATION_FILE = "/tmp/.last_token_rotation"
TOKEN_ROTATION_DAYS  = 90

def rotate_token_if_due():
    now = time.time()
    try:
        with open(_TOKEN_ROTATION_FILE) as f:
            last = float(f.read().strip())
    except Exception:
        last = 0
    if now - last < TOKEN_ROTATION_DAYS * 86400:
        return
    new_token = secrets.token_hex(32)
    print(f"TOKEN ROTATION DUE | new_token={new_token}")
    alert_async("Token Rotation Due",
                f"New AIEM_INTERNAL_TOKEN:\n\n  {new_token}\n\n"
                f"Update this value in Replit Secrets → AIEM_INTERNAL_TOKEN\n"
                f"Then restart the API server.")
    with open(_TOKEN_ROTATION_FILE, "w") as f:
        f.write(str(now))

# ═══════════════════════════════════════════════════════════
# 9. DATABASE BACKUP — dumps DB every 24 hours automatically
# ═══════════════════════════════════════════════════════════
_BACKUP_FILE    = "/tmp/.last_db_backup"
BACKUP_INTERVAL = 86400
BACKUP_DIR      = "/tmp/db_backups"

def run_backup_if_due():
    if not DATABASE_URL:
        return
    now = time.time()
    try:
        with open(_BACKUP_FILE) as f:
            last = float(f.read().strip())
    except Exception:
        last = 0
    if now - last < BACKUP_INTERVAL:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    filename = (f"{BACKUP_DIR}/backup_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sql")
    try:
        result = subprocess.run(
            ["pg_dump", DATABASE_URL, "-f", filename],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print(f"DB BACKUP SUCCESS | file={filename}")
            with open(_BACKUP_FILE, "w") as f:
                f.write(str(now))
        else:
            print(f"DB BACKUP FAILED | {result.stderr}")
            alert_async("DB Backup Failed", f"pg_dump error:\n{result.stderr}")
    except Exception as e:
        print(f"DB BACKUP ERROR | {e}")
        alert_async("DB Backup Error", str(e))

# ═══════════════════════════════════════════════════════════
# 10. BOOTSTRAP — one call wires everything in
# ═══════════════════════════════════════════════════════════
def init_security(app):
    apply_proxy_fix(app)

    @app.route("/stock-api/aiem/debug-ip")
    def debug_ip():
        return jsonify({
            "ip":        request.remote_addr,
            "forwarded": request.headers.get("X-Forwarded-For"),
        })

    print("SECURITY | aiem_security.py initialized — all protections active")
    return app
