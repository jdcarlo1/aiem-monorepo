"""
aiem_provenance.py

Proves that a given JSON payload was produced by AIEM's actual runtime
process (this file, running inside main.py), not fabricated/paraphrased by
an agent (Replit's or otherwise).

HOW IT WORKS
------------
1. Inside AIEM's real process (not a wrapper, not an agent summarizing it),
   call `sign_payload()` on the result the moment it's computed.
2. This produces an HMAC-SHA256 signature over the exact bytes of the JSON,
   using a secret key that lives ONLY in this environment (Replit Secret
   AIEM_SIGNING_KEY — never hardcoded, never logged, never given to any LLM
   agent).
3. Store/return {payload, timestamp, nonce, source, signature}.
4. Anyone (you, me, an auditor) can independently run `verify_payload()`.

   IMPORTANT: HMAC is symmetric, so "verification key" == "signing key".
   That means the verifier must also hold the secret. This proves
   "produced by something holding AIEM_SIGNING_KEY" — it does NOT
   cryptographically distinguish AIEM from anything else that also has
   the key. If true non-repudiation is ever needed (only AIEM can sign,
   anyone can verify without the secret), switch to Ed25519 asymmetric
   signing instead. HMAC is sufficient here because the only holder of
   AIEM_SIGNING_KEY is this process itself.

KEY ROTATION
------------
`rotate_signing_key(rotated_by)` generates a new random 256-bit key,
updates the in-process SIGNING_KEY global, and writes a row to the
`signing_key_events` table with SHA-256 hashes of both old and new keys
(never raw keys). The new key MUST then be stored in the Replit Secret
AIEM_SIGNING_KEY out-of-band — this process cannot write Replit Secrets.
Until the secret is updated and the process restarts, in-process signing
will use the new key but a fresh restart would revert to the old one.

NO GRACE PERIOD: once rotate_signing_key() is called, all subsequent
verify_payload() calls use only the new key. Any envelope signed with the
old key will immediately fail verification. There is no dual-key window by
design — an unexpired old key is a security liability, not a convenience.
If in-flight records signed with the old key need to be preserved, the
caller should re-sign them before calling rotate_signing_key().

INTEGRATION POINT
------------------
Wherever AIEM currently returns a result dict (e.g. from _get_sector_heat,
pick_scores, or any decision AIEM logs), wrap it with sign_payload() BEFORE
it leaves AIEM's process — not after Replit's agent has touched it.
"""

import hmac
import hashlib
import json
import os
import secrets
import time
import uuid
from typing import Optional


# Load from environment ONLY. Never hardcode, never print, never pass to
# any LLM agent (including the one wiring this in) — that would defeat the
# whole point.
SIGNING_KEY = os.environ.get("AIEM_SIGNING_KEY")


_SIGNING_KEY_DDL = """
CREATE TABLE IF NOT EXISTS signing_key_events (
    id            SERIAL PRIMARY KEY,
    event_type    TEXT NOT NULL,
    old_key_hash  TEXT,
    new_key_hash  TEXT NOT NULL,
    rotated_by    TEXT NOT NULL,
    rotated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes         TEXT
);
"""


def init_provenance_schema() -> None:
    """
    Idempotent: creates signing_key_events table if it does not exist.
    Call once at startup. Uses AIEM_DATABASE_URL or DATABASE_URL.
    """
    db_url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("[aiem_provenance] no DATABASE_URL — skipping schema init")
        return
    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute(_SIGNING_KEY_DDL)
        print("[aiem_provenance] signing_key_events table ready")
    except Exception as exc:
        print(f"[aiem_provenance] schema init failed (non-fatal): {exc}")


def _key_hash(key: str) -> str:
    """SHA-256 of the key, hex-encoded. Safe to store/log — not reversible."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def rotate_signing_key(rotated_by: str = "system", notes: Optional[str] = None) -> dict:
    """
    Generate a new 256-bit signing key, update the in-process global, and
    write an audit row to signing_key_events.

    Returns a dict with:
      - old_key_hash: SHA-256 of the OLD key (redacted — never the raw key)
      - new_key_hash: SHA-256 of the NEW key
      - rotated_at: UTC ISO timestamp of the rotation
      - action_required: reminder string

    IMPORTANT: after this call, update Replit Secret AIEM_SIGNING_KEY to the
    new raw key value returned by os.urandom(32).hex() — this function cannot
    write Replit Secrets for you. Until the secret is updated and the process
    restarts, only the current running process uses the new key.

    NO GRACE PERIOD: old key immediately stops verifying. Re-sign any
    in-flight records before calling this if you need them to remain valid.
    """
    global SIGNING_KEY

    old_key  = SIGNING_KEY
    new_key  = secrets.token_hex(32)   # 256 bits of CSPRNG — never logged raw

    old_hash = _key_hash(old_key) if old_key else None
    new_hash = _key_hash(new_key)

    SIGNING_KEY = new_key

    rotated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    db_url = os.environ.get("AIEM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    db_written = False
    if db_url:
        try:
            import psycopg2
            with psycopg2.connect(db_url, connect_timeout=4) as conn:
                with conn.cursor() as cur:
                    cur.execute(_SIGNING_KEY_DDL)
                    cur.execute("""
                        INSERT INTO signing_key_events
                            (event_type, old_key_hash, new_key_hash, rotated_by, notes)
                        VALUES (%s, %s, %s, %s, %s)
                    """, ("rotation", old_hash, new_hash, rotated_by, notes))
            db_written = True
            print(f"[aiem_provenance] key rotation logged to signing_key_events (rotated_by={rotated_by})")
        except Exception as exc:
            print(f"[aiem_provenance] WARNING: key rotated in memory but DB write failed: {exc}")

    return {
        "old_key_hash": old_hash,
        "new_key_hash": new_hash,
        "rotated_at": rotated_at,
        "rotated_by": rotated_by,
        "db_written": db_written,
        "action_required": (
            "Update Replit Secret AIEM_SIGNING_KEY with the new raw key value, "
            "then restart the process. Until then, only this running process uses "
            "the new key. Old key is immediately invalid — no grace period."
        ),
    }


def _canonical_bytes(payload: dict) -> bytes:
    """Deterministic serialization so signature is reproducible."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sign_payload(payload: dict) -> dict:
    """
    Call this INSIDE AIEM's actual process, right where the data is
    computed (e.g. right after _get_sector_heat() returns, before any
    agent or wrapper touches it).
    """
    if not SIGNING_KEY:
        raise RuntimeError(
            "AIEM_SIGNING_KEY not set in this environment. "
            "If this raises inside AIEM's real process, your key isn't "
            "provisioned correctly — fix that before trusting any signature."
        )

    envelope = {
        "payload": payload,
        "timestamp": int(time.time()),
        "nonce": uuid.uuid4().hex,
        "source": "aiem",
    }
    sig = hmac.new(
        SIGNING_KEY.encode("utf-8"),
        _canonical_bytes(envelope),
        hashlib.sha256,
    ).hexdigest()

    envelope["signature"] = sig
    return envelope


def verify_payload(envelope: dict, max_age_seconds: int = 300) -> dict:
    """
    Run this INDEPENDENTLY (ideally a code path that isn't the one that
    generated the data) to check whether the signature is valid and fresh.

    Returns a dict with verified: bool and reason: str.
    """
    if not SIGNING_KEY:
        return {"verified": False, "reason": "AIEM_SIGNING_KEY not set for verifier"}

    received_sig = envelope.get("signature")
    if not received_sig:
        return {"verified": False, "reason": "no signature present"}

    check_envelope = {k: v for k, v in envelope.items() if k != "signature"}
    expected_sig = hmac.new(
        SIGNING_KEY.encode("utf-8"),
        _canonical_bytes(check_envelope),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(received_sig, expected_sig):
        return {"verified": False, "reason": "signature mismatch — payload was altered or not signed by holder of AIEM_SIGNING_KEY"}

    age = time.time() - envelope.get("timestamp", 0)
    if age > max_age_seconds:
        return {"verified": False, "reason": f"signature valid but stale ({age:.0f}s old, max {max_age_seconds}s) — could be replayed"}

    return {"verified": True, "reason": "signature valid and fresh", "age_seconds": round(age, 1)}
