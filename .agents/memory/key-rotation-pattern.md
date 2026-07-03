---
name: AIEM signing key rotation
description: How rotate_signing_key() works and its limitations in Replit environment
---

## Rule
`rotate_signing_key(rotated_by)` in `aiem_provenance.py`:
- Generates `secrets.token_hex(32)` (256-bit CSPRNG)
- Stores SHA-256 hashes of old/new key in `signing_key_events` table (never raw keys)
- Updates `SIGNING_KEY` global immediately — old key is immediately invalid
- NO grace period by design; re-sign in-flight records BEFORE calling

**Why:** A dual-key window is a security liability. Any old-key-signed record that arrives after rotation is suspicious by definition.

**Limitation:** Cannot change Replit Secrets from within the process. After rotation, the `AIEM_SIGNING_KEY` secret must be updated out-of-band, or the next restart will revert to the old key. This is documented in the return dict's `action_required` field.

## How to apply
After calling rotate_signing_key():
1. Copy the new raw key from the in-memory SIGNING_KEY (or re-generate from the same CSPRNG call if captured)
2. Update Replit Secret AIEM_SIGNING_KEY manually
3. Restart the process

The `signing_key_events` table row provides the audit trail (old/new hashes, rotated_at, rotated_by).
