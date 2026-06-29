---
name: AIEM speed fixes
description: Four performance improvements to the AIEM Quant Agent chat loop
---

## Fast-path for casual questions
- `_classify_question_complexity` returns 1 for messages <15 words with no uppercase tickers and no analytical keywords
- When max_iters==1: `aiem_chat_start` uses a simple conversational prompt (no review_own_accuracy instruction); `_run_aiem_focused_session` passes `tool_choice="none"` so LLM answers directly in 1 LLM call (~3s vs 30-60s)
- "does it work" removed from deep keyword list (matched "how does it work" as false positive); replaced with "statistically"

**Why:** The mandatory `review_own_accuracy` call in the system prompt consumed one full iteration; with max_iters=1 there was no iteration left for the text response → fallback message. Fix: skip that prompt path entirely for casual questions.

## Batching (already correct)
- `_aiem_tool_get_live_snapshot` already accepts a list and sends all tickers in one Polygon API call (`?tickers=A,B,C`). No change needed.

## 45s live snapshot cache
- `_LIVE_SNAPSHOT_CACHE` dict + `_LIVE_SNAPSHOT_TTL=45` added before `_aiem_tool_get_live_snapshot`
- Key: `frozenset(tickers)` (order-independent). Hit: returns cached result with `_cache_age_s` field
- Stale eviction: entries older than 4× TTL pruned on each write

## Parallel tool dispatch
- Replaced sequential `for tc in msg.tool_calls` loop with `ThreadPoolExecutor(max_workers=min(n,5))`
- Results collected via `as_completed`, then merged back in ORIGINAL `msg.tool_calls` order before `messages.append` — OpenAI requires tool results in same order as tool_calls
- 2 tools @ 1.5s+1.0s: 2.50s sequential → 1.51s parallel (1.66×)
