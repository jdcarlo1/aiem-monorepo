---
name: AIEM Quant Agent token streaming
description: How real OpenAI token streaming was wired into _run_aiem_focused_session and the SSE chat endpoint
---

## Rule
`_run_aiem_focused_session` now has an `on_token=None` parameter. When set, all LLM calls use `stream=True`. Tool-call iterations emit no content tokens naturally (OpenAI sends no `delta.content` when the model is calling tools), so `on_token` only fires during the final text response. Non-streaming path is unchanged when `on_token` is None.

**Why:** Real streaming needed — first tokens appear within ~100ms of the model starting to write, vs 10–15s wait for the full response with non-streaming.

## Streaming tool-call reconstruction
When `stream=True`, tool calls arrive as chunked deltas. Accumulate with an index-keyed dict, then build attribute-compatible proxy objects:
```python
class _SF: def __init__(s, n, a): s.name = n; s.arguments = a
class _STC: def __init__(s, i, n, a): s.id = i; s.type = "function"; s.function = _SF(n, a)
```
These are passed to `_exec_one_tool` which accesses `.function.name` and `.function.arguments` — same attributes the real SDK objects have.

## SSE endpoint
`POST /stock-api/aiem/chat/stream` — runs session in daemon thread, communicates via `queue.Queue`:
- `{"type":"started","job_id":"..."}` — immediately
- `{"type":"tool","tool":"...","pre":bool}` — before/after each tool
- `{"type":"token","token":"..."}` — each token of the final LLM response
- `{"type":"done","answer":"...","tool_count":N}` — session complete
- `{"type":"error","error":"..."}` — on failure
- `{"type":"heartbeat"}` — every 90s idle to keep connection alive

## Frontend
`handleSubmit` uses `fetch` + `ReadableStream` + `TextDecoder` to read SSE.
`QASession.streaming_text` accumulates tokens. `SessionBubble` renders it with a blinking cursor during the running state. Falls back to `startPolling(jobId)` if stream drops mid-session.

## Gotcha
`_decide_max_iters` does NOT exist as a global. The correct global function is `_classify_question_complexity(question)`. Do NOT copy the local alias from `aiem_chat_start`.
