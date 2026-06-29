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
    job_id            — the AIEM job UUID
    unix_timestamp    — the signed_ts string stored at signing time
    openai_response_id — the chatcmpl-... ID from OpenAI
    response          — the answer text (will be .strip()ped before verify)
    aiem_signature    — the HMAC-SHA256 hex digest

Any missing field or signature mismatch raises ValueError and blocks the result.
Every successful verification is written to aiem_verification_log.
"""

import hmac
import hashlib
import os
import psycopg2
from functools import wraps

AIEM_SECRET = os.environ.get("AIEM_SECRET", "")
_DB_URL     = os.environ.get("DATABASE_URL", "")


def _log_verified_response(job_id: str, unix_timestamp: str,
                            openai_response_id: str, client_ip: str = None) -> None:
    """Write a permanent audit record. Silently swallows DB errors so a log
    failure never blocks a legitimate response."""
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as _c, _c.cursor() as _cu:
            _cu.execute(
                """
                INSERT INTO aiem_verification_log
                    (job_id, unix_timestamp, openai_response_id, client_ip)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (job_id, unix_timestamp, openai_response_id, client_ip),
            )
            _c.commit()
    except Exception:
        pass  # audit log failure must never surface to caller


def verify_aiem_response(result: dict, client_ip: str = None) -> dict:
    """
    Verify a single AIEM poll result dict.
    Returns the result unchanged if valid; raises ValueError otherwise.
    """
    if not AIEM_SECRET:
        raise ValueError("AIEM_SECRET not set — cannot verify response")

    job_id    = result.get("job_id")
    timestamp = result.get("signed_ts") or result.get("unix_timestamp")
    openai_id = result.get("openai_response_id")
    response  = (result.get("answer") or result.get("response") or "").strip()
    signature = result.get("aiem_signature")

    if not all([job_id, timestamp, openai_id, response, signature]):
        missing = [k for k, v in {
            "job_id": job_id, "unix_timestamp": timestamp,
            "openai_response_id": openai_id, "response": response,
            "aiem_signature": signature,
        }.items() if not v]
        raise ValueError(
            f"AIEM response missing required verification fields: {missing} — BLOCKED"
        )

    payload  = f"{job_id}:{timestamp}:{openai_id}:{response}"
    expected = hmac.new(
        AIEM_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise ValueError(
            "AIEM signature mismatch — response BLOCKED, possible tampering"
        )

    _log_verified_response(job_id, timestamp, openai_id, client_ip)
    return result


def require_aiem_verification(func):
    """
    Decorator — wrap ANY function that returns an AIEM poll result dict.
    Verification is performed before the result is returned to the caller.
    If the response cannot be verified it is blocked (ValueError raised).

    Example:
        @require_aiem_verification
        def ask_aiem(question: str) -> dict:
            ...poll /stock-api/aiem/chat/{job_id}...
            return poll_result_dict
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        return verify_aiem_response(result)
    return wrapper
