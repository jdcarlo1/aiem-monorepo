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
import time
import uuid


# Load from environment ONLY. Never hardcode, never print, never pass to
# any LLM agent (including the one wiring this in) — that would defeat the
# whole point.
SIGNING_KEY = os.environ.get("AIEM_SIGNING_KEY")


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
        "nonce": uuid.uuid4().hex,   # prevents replay of an old signed payload
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

    # constant-time compare — avoid timing side-channel
    if not hmac.compare_digest(received_sig, expected_sig):
        return {"verified": False, "reason": "signature mismatch — payload was altered or not signed by holder of AIEM_SIGNING_KEY"}

    age = time.time() - envelope.get("timestamp", 0)
    if age > max_age_seconds:
        return {"verified": False, "reason": f"signature valid but stale ({age:.0f}s old, max {max_age_seconds}s) — could be replayed"}

    return {"verified": True, "reason": "signature valid and fresh", "age_seconds": round(age, 1)}
