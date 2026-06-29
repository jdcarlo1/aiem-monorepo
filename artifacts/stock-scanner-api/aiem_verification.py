"""
aiem_verification.py
Client-side AIEM response verification decorator + audit trail.

Usage:
    from aiem_verification import require_aiem_verification

    @require_aiem_verification
    def ask_aiem(question: str) -> dict:
        # poll /stock-api/aiem/chat/{job_id} until done, return the JSON dict
        ...

The decorated function must return a dict containing:
    job_id             — the AIEM job UUID
    signed_ts          — exact unix timestamp string stored at signing time
    openai_response_id — the chatcmpl-... ID from OpenAI
    answer             — the response text (will be .strip()ped before verify)
    aiem_signature     — the HMAC-SHA256 hex digest

Any missing field or signature mismatch raises ValueError and BLOCKS the return.
Every verification attempt (pass AND fail) is written to aiem_verification_log.
"""

import hmac
import hashlib
import os
import psycopg2
from functools import wraps

AIEM_SECRET = os.environ.get("AIEM_SECRET", "")
_DB_URL     = os.environ.get("DATABASE_URL", "")


def _detect_client_ip() -> str | None:
    """
    Best-effort client IP detection.
    1. If we're inside a Flask request context, read X-Forwarded-For.
    2. Otherwise return None — the caller can pass client_ip= explicitly.
    """
    try:
        from flask import request as _req
        fwd = _req.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() or _req.remote_addr or None
    except Exception:
        return None


def _write_audit_row(job_id: str, unix_timestamp: str,
                     openai_response_id: str, client_ip: str | None,
                     verified: bool, failure_reason: str | None) -> None:
    """
    Write one row to aiem_verification_log — both successes AND failures.
    Silently swallows DB errors so a log failure never surfaces to the caller.
    """
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as _c, _c.cursor() as _cu:
            _cu.execute(
                """
                INSERT INTO aiem_verification_log
                    (job_id, unix_timestamp, openai_response_id,
                     client_ip, verified, failure_reason)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (job_id, unix_timestamp, openai_response_id,
                 client_ip, verified, failure_reason),
            )
            _c.commit()
    except Exception:
        pass


def verify_aiem_response(result: dict, client_ip: str | None = None) -> dict:
    """
    Verify a single AIEM poll result dict.

    - Returns the result unchanged on success.
    - Raises ValueError on any failure (missing fields, bad signature,
      missing secret). The exception message is safe to surface to callers.
    - client_ip: pass explicitly, or leave None to auto-detect from Flask context.
    - Every call (pass or fail) is written to aiem_verification_log.
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
        reason = "HMAC mismatch"
        _write_audit_row(job_id, timestamp, openai_id, ip, False, reason)
        raise ValueError(
            "AIEM signature mismatch — response BLOCKED, possible tampering"
        )

    _write_audit_row(job_id, timestamp, openai_id, ip, True, None)
    return result


def require_aiem_verification(func):
    """
    Decorator — wrap ANY function that returns an AIEM poll result dict.
    Verification runs before the result reaches the caller.
    Failures raise ValueError and block the return entirely.

    The originating request IP is auto-detected from Flask context when
    the decorated function is called inside a Flask request handler.
    Pass client_ip= to verify_aiem_response() directly if calling outside Flask.

    Example:
        @require_aiem_verification
        def ask_aiem(question: str) -> dict:
            # poll /stock-api/aiem/chat/{job_id} until status == "done"
            return poll_result_dict   # fully resolved, not in-flight
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        return verify_aiem_response(result)
    return wrapper
