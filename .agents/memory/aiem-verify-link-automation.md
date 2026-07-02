---
name: AIEM auto-minted verify links + history page
description: How permanent, browser-tappable verification got wired into every AIEM response (not just one-off admin-minted links)
---

Every completed+signed `quant_agent_sessions` row now auto-mints its own verify-link
token inside `_qa_db_update` (shared helper `_mint_verify_link_token`, same function
the admin mint endpoint uses). The token + expiry are stored directly on the session
row (`verify_token`, `verify_token_expires_at`) so `GET /stock-api/aiem/chat/<job_id>`
can build a full tappable `verify_url` with zero extra DB joins or admin calls.

**Why:** the user explicitly rejected one-off/manual verify links as the end state —
they wanted proof embedded automatically in every response, tappable from a phone
browser, without asking for a link each time. But an unexpiring token would be a
standing credential risk, so the design keeps it *long-lived but still expiring*
(7 days default, 30-day hard cap on manual override) rather than permanent.

**How to apply:** any new "give proof automatically" feature for this pipeline
should extend this same auto-mint-on-completion pattern rather than adding another
manual/admin-triggered link type. The `/stock-api/aiem/history` page (its own
separate, non-ADMIN_TOKEN token via `/admin/mint-history-link`) reuses each job's
already-stored `verify_token` for its per-row links — it does NOT mint new ones,
so old jobs that finished before this feature existed correctly show "link expired"
rather than a broken link. Same admin-token-route-consistency rule applies to all
sibling mint endpoints (identical X-API-Key/`?api_key=` check copied verbatim).
