#!/usr/bin/env python3
"""
Standalone HMAC-SHA256 verifier for the M7 audit evidence bundle.

Reads:
  verification/evidence_payload.json   -- the signed envelope (sans signature field)
  verification/reported_signature.txt  -- the hex signature to check against

Recomputes the signature using AIEM_SIGNING_KEY (must be set in env).
Prints both the computed and reported hashes, then MATCH or NO MATCH.

Usage:
  AIEM_SIGNING_KEY=<key> python3 verification/verify_signature.py
"""
import hmac
import hashlib
import json
import os
import sys

PAYLOAD_FILE   = os.path.join(os.path.dirname(__file__), "evidence_payload.json")
SIGNATURE_FILE = os.path.join(os.path.dirname(__file__), "reported_signature.txt")

def canonical_bytes(obj: dict) -> bytes:
    """Deterministic JSON serialization — same algorithm as aiem_provenance.sign_payload()."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")

def main():
    signing_key = os.environ.get("AIEM_SIGNING_KEY", "")
    if not signing_key:
        print("ERROR: AIEM_SIGNING_KEY is not set in the environment.", file=sys.stderr)
        sys.exit(1)

    with open(PAYLOAD_FILE, "r") as f:
        envelope = json.load(f)

    with open(SIGNATURE_FILE, "r") as f:
        reported_sig = f.read().strip()

    canon = canonical_bytes(envelope)
    computed_sig = hmac.new(
        signing_key.encode("utf-8"),
        canon,
        hashlib.sha256,
    ).hexdigest()

    print(f"Canonical input : {canon.decode('utf-8')}")
    print()
    print(f"Computed  hash  : {computed_sig}")
    print(f"Reported  hash  : {reported_sig}")
    print()
    if computed_sig == reported_sig:
        print("MATCH")
    else:
        print("NO MATCH")

if __name__ == "__main__":
    main()
