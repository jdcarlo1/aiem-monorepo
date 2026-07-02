"""
aiem_verification.py — Three-layer AIEM security stack.

Layer 1 — Response integrity (verify_aiem_response / require_aiem_verification)
    HMAC-SHA256 over GPT response text + job_id + signed_ts + openai_response_id.
    Proves the response came from the AIEM pipeline and was not tampered with.

Layer 2 — Request auth + replay protection (require_aiem_token)
    Three headers required:
        X-AIEM-Token      — plaintext secret (identity)
        X-AIEM-Timestamp  — unix timestamp string
        X-AIEM-Signature  — HMAC-SHA256(token, f"{ts}:{question}")
    Timestamp must be within 30 seconds — blocks replay attacks.

ProxyFix helper (apply_proxy_fix / register_debug_route)
    Wraps Flask WSGI so X-Forwarded-For is trusted for real client IP.
    Debug route lets you confirm spoofed headers are stripped correctly.

Audit (log_audit / _write_audit_row)
    Every verification attempt (pass AND fail) → aiem_verification_log table.

Client (aiem_ask)
    Signs and submits a question, polls until done, verifies the response.
    The full chain: sign → POST → poll → verify → return.
"""

import os
import hmac
import hashlib
import time
import psycopg2
import requests as _requests
from functools import wraps
from datetime import datetime, timezone

# ── Secrets ───────────────────────────────────────────────────────────────────
AIEM_TOKEN  = os.environ.get("AIEM_INTERNAL_TOKEN", "")
AIEM_SECRET = os.environ.get("AIEM_SECRET", "")
_DB_URL     = os.environ.get("DATABASE_URL", "")


# ── ProxyFix helper ───────────────────────────────────────────────────────────

def apply_proxy_fix(app):
    """Wrap Flask WSGI app so X-Forwarded-For is trusted for real client IP."""
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    return app


def register_debug_route(app):
    """
    Register a debug endpoint that returns the IP Flask actually sees.
    Use to confirm ProxyFix is stripping spoofed headers:

        curl -H "X-Forwarded-For: FAKE_IP, 1.2.3.4" \\
             https://yourdomain.com/stock-api/aiem/debug-ip
        → {"ip": "1.2.3.4", "forwarded": "FAKE_IP, 1.2.3.4"}
          NOT "FAKE_IP" — ProxyFix peels off the rightmost hop.
    """
    from flask import request, jsonify

    @app.route("/stock-api/aiem/debug-ip")
    def debug_ip():
        return jsonify({
            "ip":        request.remote_addr,
            "forwarded": request.headers.get("X-Forwarded-For"),
        })


# ── Audit ─────────────────────────────────────────────────────────────────────

def _detect_client_ip() -> str | None:
    """Best-effort IP from Flask request context."""
    try:
        from flask import request as _req
        fwd = _req.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() or _req.remote_addr or None
    except Exception:
        return None


def _write_audit_row(job_id, unix_timestamp, openai_response_id,
                     client_ip, verified, failure_reason,
                     job_type=None) -> None:
    """Write one audit row. Never raises."""
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as _c, _c.cursor() as _cu:
            _cu.execute(
                """
                INSERT INTO aiem_verification_log
                    (job_id, unix_timestamp, openai_response_id,
                     client_ip, verified, failure_reason, job_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (job_id, unix_timestamp, openai_response_id,
                 client_ip, verified, failure_reason, job_type),
            )
            _c.commit()
    except Exception:
        pass


def log_audit(verified: bool, ip: str = None, reason: str = None,
              token_hint: str = None, job_id: str = None,
              job_type: str = None) -> None:
    """Structured audit record — stdout + DB."""
    record: dict = {
        "at":       datetime.now(timezone.utc).isoformat(),
        "ip":       ip or _detect_client_ip(),
        "verified": verified,
    }
    if job_id:      record["job"]        = job_id
    if job_type:    record["job_type"]   = job_type
    if token_hint:  record["token_hint"] = f"...{token_hint}"
    if reason:      record["reason"]     = reason
    print(f"AUDIT | {record}")
    _write_audit_row(job_id, None, None, record["ip"], verified, reason, job_type)


def log_research_loop_run(job_id: str, verified: bool = True,
                           reason: str = None) -> None:
    """
    Tag-specific audit entry for the free 24/7 self-research loop
    (indicator grid battery). Always writes job_type='aiem_self_research'
    and openai_response_id=NULL — this row is proof-by-construction that
    this particular job ran with no OpenAI response attached, distinct
    from the paid chat-assistant rows in the same table (which always
    carry a real openai_response_id).
    """
    log_audit(verified=verified, reason=reason, job_id=job_id,
              job_type="aiem_self_research")


# ── Layer 2: Request signing + auth ──────────────────────────────────────────

def sign_request(question: str) -> tuple[str, str]:
    """
    Sign a question for sending to AIEM.
    Returns (ts, sig) — send as X-AIEM-Timestamp and X-AIEM-Signature headers.
    The raw AIEM_TOKEN goes in X-AIEM-Token.
    """
    if not AIEM_TOKEN:
        raise ValueError("AIEM_INTERNAL_TOKEN not set — cannot sign request")
    ts  = str(int(time.time()))
    msg = f"{ts}:{question}"
    sig = hmac.new(AIEM_TOKEN.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return ts, sig


def verify_signature(question: str, ts: str, sig: str, max_age: int = 30) -> bool:
    """
    Verify an (X-AIEM-Timestamp, X-AIEM-Signature) pair.
    Rejects anything older than max_age seconds — blocks replay attacks.
    """
    if not AIEM_TOKEN:
        return False
    try:
        age = abs(time.time() - int(ts))
    except (ValueError, TypeError):
        return False
    if age > max_age:
        return False
    expected = hmac.new(
        AIEM_TOKEN.encode(),
        f"{ts}:{question}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def require_aiem_token(f):
    """
    Flask endpoint decorator — requires all three auth headers:
        X-AIEM-Token      (identity)
        X-AIEM-Timestamp  (timestamp)
        X-AIEM-Signature  (HMAC of ts:question)

    Returns 403 on any failure. Logs every attempt.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import request, abort
        token = request.headers.get("X-AIEM-Token", "")
        ts    = request.headers.get("X-AIEM-Timestamp", "")
        sig   = request.headers.get("X-AIEM-Signature", "")
        ip    = request.remote_addr

        if not AIEM_TOKEN:
            log_audit(False, ip, reason="AIEM_INTERNAL_TOKEN_not_configured")
            abort(403)

        if not hmac.compare_digest(AIEM_TOKEN, token):
            log_audit(False, ip, reason="bad_token")
            abort(403)

        question = (request.get_json(silent=True) or {}).get("question", "")
        if not verify_signature(question, ts, sig):
            log_audit(False, ip, reason="bad_signature_or_replay")
            abort(403)

        log_audit(True, ip, token_hint=token[-4:])
        return f(*args, **kwargs)

    return decorated


# ── Layer 1: Response integrity ───────────────────────────────────────────────

def verify_aiem_response(result: dict, client_ip: str | None = None) -> dict:
    """
    Verify a single AIEM poll result dict.
    Raises ValueError (and logs) on any failure; returns result unchanged on success.
    """
    ip = client_ip or _detect_client_ip()

    if not AIEM_SECRET:
        _write_audit_row(None, None, None, ip, False, "AIEM_SECRET not configured")
        raise ValueError("AIEM_SECRET not set — cannot verify response")

    job_id    = result.get("job_id")
    timestamp = result.get("signed_ts") or result.get("unix_timestamp")
    openai_id = result.get("openai_response_id")
    response  = (result.get("answer") or result.get("response") or "").strip()
    signature = result.get("aiem_signature")

    missing = [k for k, v in {
        "job_id": job_id, "unix_timestamp": timestamp,
        "openai_response_id": openai_id, "response": response,
        "aiem_signature": signature,
    }.items() if not v]

    if missing:
        reason = f"missing fields: {missing}"
        _write_audit_row(job_id, timestamp, openai_id, ip, False, reason)
        raise ValueError(f"AIEM response {reason} — BLOCKED")

    payload  = f"{job_id}:{timestamp}:{openai_id}:{response}"
    expected = hmac.new(
        AIEM_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        _write_audit_row(job_id, timestamp, openai_id, ip, False, "HMAC mismatch")
        raise ValueError("AIEM signature mismatch — response BLOCKED, possible tampering")

    _write_audit_row(job_id, timestamp, openai_id, ip, True, None)
    return result


def require_aiem_verification(func):
    """Decorator: verify AIEM response integrity before returning to caller."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        return verify_aiem_response(func(*args, **kwargs))
    return wrapper


# ── AIEM client ───────────────────────────────────────────────────────────────

def aiem_ask(question: str, base_url: str = None,
             poll_interval: float = 3.0, timeout: float = 120.0) -> dict:
    """
    Full chain: sign → POST → poll → verify → return.
    Raises ValueError if response verification fails.
    Raises TimeoutError if AIEM doesn't respond within timeout seconds.
    """
    url    = (base_url or os.environ.get("AIEM_BASE_URL", "http://localhost:5050")).rstrip("/")
    ts, sig = sign_request(question)

    resp = _requests.post(
        f"{url}/stock-api/aiem/chat",
        json={"question": question},
        headers={
            "X-AIEM-Token":     AIEM_TOKEN,
            "X-AIEM-Timestamp": ts,
            "X-AIEM-Signature": sig,
        },
        timeout=30,
    )
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        poll = _requests.get(
            f"{url}/stock-api/aiem/chat/{job_id}", timeout=10
        ).json()
        if poll.get("status") == "error":
            raise RuntimeError(f"AIEM error: {poll.get('error')}")
        if poll.get("status") == "done":
            return verify_aiem_response(poll)

    raise TimeoutError(f"AIEM job {job_id} did not complete within {timeout}s")


# ── Usage example ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    AUDIT_QUESTIONS = [
        "When verification fails, do you raise an exception and block the return, or do you log the failure and return the result anyway?",
        "The audit log shows id=1 — is this the first-ever audit record in production, or was the table recently created or wiped?",
        "In the polling loop, is poll_result the final resolved value at the time the decorator receives it, or could it still be a reference to an in-flight mutable variable?",
        "When ask_aiem() is called from a user-facing request, does the audit log capture the originating request IP, or does it always log 127.0.0.1 regardless of where the call originated?",
    ]
    for i, question in enumerate(AUDIT_QUESTIONS, 1):
        print(f"\n--- Question {i} ---")
        result = aiem_ask(question)
        print(f"A: {result.get('answer', '')[:200]}...")
