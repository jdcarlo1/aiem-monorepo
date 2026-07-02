---
name: Admin-token route consistency
description: When adding a new HTTP route next to an existing admin-gated one, always copy the exact same auth check — don't assume a read-only sibling route is lower-risk.
---

Adding a GET "verify" or "status" route next to an existing admin-token-gated POST route on the same resource is easy to leave unguarded, reasoning that it's "just a read" or "doesn't leak the full payload." A skeptical/hostile auditor (or automated scanner) will specifically probe sibling routes on a resource they know is sensitive, and an inconsistency between two routes on the same path prefix is exactly the kind of thing that gets flagged first.

**Why:** even a metadata-only leak (ticker, row_id, verification status) undermines a "this endpoint is protected" claim if a neighboring route on the same resource has no check at all — it looks like an oversight, not a design choice, which erodes trust in the whole feature.

**How to apply:** whenever adding a new route under an existing admin/token-gated `/admin/...` or similar prefix, copy the exact `hmac.compare_digest(token, expected)` (or equivalent) check verbatim before writing the route body, then verify with curl: no-token → 403, wrong-token → 403, correct-token → 200.
