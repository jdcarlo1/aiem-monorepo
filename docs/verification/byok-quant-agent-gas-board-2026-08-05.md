# BYOK Quant Agent + Gas Board integration (2026-08-05)

## Intended product rule

Quant Agent uses heavy tool/data calls → **subscriber pays OpenAI** via BYOK.  
Gas Board scores from Neon/platform market tables → **subscriber token only** (no OpenAI key).

## Fixes

| Change | Detail |
|---|---|
| Quant chat auth | `_byok_resolve_chat_auth` — missing token **401**, invalid token **403**, missing OpenAI key **402**, decrypt fail **500** |
| No platform burn | Browser Quant Agent never falls through to platform OpenAI key |
| Stream + poll | Both gated the same way (internal `X-AIEM-Token` still allowed on poll) |
| Session ownership | `quant_agent_session_owners` maps job → subscriber; history filtered by token |
| PUT `/user/keys` | Token-only validate OK; `sk-` prefix check on OpenAI key |
| Gas Board UI | Inline subscriber-token field; copy no longer says “API key” |
| Quant UI | Requires token before send; distinct banners; shows server error text; history passes token |

## Subscriber setup (after deploy)

1. Open **Quant Agent** → ⚙️ → **API Keys**
2. Paste `sub_…` from welcome email
3. Paste OpenAI `sk-…` → **Save Keys**
4. Chat works on their key
5. **Gas Board** uses the same token (field on the tab) — no OpenAI key needed
