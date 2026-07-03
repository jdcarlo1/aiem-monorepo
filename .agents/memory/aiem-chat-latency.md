---
name: AIEM chat latency profile
description: Profiling results for the AIEM research path; what's slow and why; fixes applied
---

## Findings (3-run profiling with per-step timing in trace)

| Phase | Time |
|-------|------|
| DB tools (review_own_accuracy, mkt_options_flow_scan) | 0–10 ms — negligible |
| OpenAI gpt-5.4 LLM calls | 1.5–52s — **all of the time** |
| Variance across runs | 5s / 57s / 9s — pure OpenAI API jitter |

DB is never the bottleneck. Every second of latency is OpenAI inference.

## Concurrent session fix (removed global Semaphore)

The `/aiem/chat` endpoint previously held a `Semaphore(1)` that serialized all user chat sessions. Removed completely — each session is isolated by `job_id` with its own DB row and OpenAI message array; no shared mutable state. SMS/email/cron still use `app._aiem_qa_lock` (owner-only, intentional).

**Result**: 10-session burst submits in 0.17s, 9/10 done concurrently. Wall time 66s vs 136s serialized.

**Remaining lock**: `app._aiem_qa_lock` (Semaphore(1)) — keep for owner SMS/email/cron only. Never wire to user-facing chat.

**120s hard deadline**: `_run_aiem_focused_session` runs in a daemon thread; outer worker does `join(timeout=120)`. If `is_alive()=True` after join, writes "error" to DB. Daemon thread continues (can't kill Python threads); completes eventually but result is discarded.

**Slow outliers are OpenAI**: RSI question hit `t_llm=29.5s` at iter=1; tool calls were 0.0–0.34s. Short squeeze ran 142s (OpenAI slow call, exceeded 120s deadline). Not a code problem — pure API jitter.

**Shared rate limiters** that concurrent sessions share: `_YF_RATE_LIMITER` (3/sec token bucket) and `_POLYGON_RATE_LIMITER`. These only matter if many sessions call yfinance/Polygon tools simultaneously. For casual questions (1-iter, no tool calls), they're never hit.

## Instrumentation added

`t_tool_s` and `t_llm_s` are now recorded in every trace step inside `_run_aiem_focused_session`. Poll endpoint exposes `tool_trace` with these fields. Session total logged on completion.

## Optimizations applied

### 3. Sync fast-path for casual 1-iter questions
- **Problem**: Every chat request went async (worker thread) even for trivial "hey are you working" messages — user had to poll for the response.
- **Fix**: Added sync fast-path in `aiem_chat_start`: when `max_iters==1 and not analysis_mode and not image_data_urls`, calls `_run_aiem_focused_session(max_iterations=1)` synchronously in the request thread. Returns `{status:"done", answer:..., sync:true}` directly in POST body — client skips polling.
- **Implementation detail**: Sync path calls `_run_aiem_focused_session` directly (NOT a reimplemented OpenAI call). This inherits model tiering (gpt-4o-mini for 1-iter), BYOK, subscriber context, and all error handling.
- **Gotcha**: `_run_aiem_focused_session` had **inconsistent return arity** — success path returned 4-tuple `(text, trace, err, openai_id)` but two early-exit paths returned 3-tuples. 4-value unpack threw `ValueError` on every call → silent fallback to async. Fixed: both early exits now return 4-tuple (added `None` as 4th value). SMS caller at line ~11660 updated to use `*_` star unpack.
- **Result**: Casual questions answered in ~2.5s (gpt-4o-mini 1-iter), no polling required.

### 4. Model tiering in _run_aiem_focused_session
- `max_iterations <= 1` → gpt-4o-mini
- `max_iterations <= 5` → gpt-4o
- `max_iterations > 5` → gpt-5.4
- Variable `_model_tier` set once before the loop, used in every `completions.create()` call.

### 1. Conditional review_own_accuracy
- **Problem**: "BEFORE answering: call review_own_accuracy" was injected into EVERY research prompt, forcing a wasted LLM round-trip (~2s) on data-retrieval questions.
- **Fix**: Only inject when question contains self-review phrases: "your accuracy", "your track record", "your win rate", "your prediction", "your picks", "your calls", "your performance", "your record", "been wrong", "been right", "how have you done", "how well have you", "calibrat", etc.
- **Why**: Single words like "call", "pick", "right" collide with market terminology (etf-calls, stock picks) — use multi-word phrases only.
- **Where**: `aiem_chat_start()` in main.py, `_wants_review` variable.

### 2. Image routing fix
- **Problem**: Image questions classified as casual (under 15 words, no analytical keywords) routed to 1-iter fast-path with prompt "this is a casual/conversational message" — no image mention → model returns empty → hits "no findings" fallback.
- **Fix**: When `image_data_url` is set, force `max_iters = max(max_iters, 3)`. Also inject "NOTE: The user has attached an image. Look at it carefully..." into the research prompt.
- **Where**: `aiem_chat_start()`, just before prompt construction.

## Demo script

`aiem_chat_demo.py` — 5-step end-to-end proof:
1. Greeting → answered in ~3s
2. Follow-up → answered in ~6s
3. Factual question → answered in ~17s
4. Image upload (48×48 red PNG) → AI says "solid red square" in ~10s
5. Post-image text → answered in ~2s, proves not stuck

Run against dev: `python3 aiem_chat_demo.py`
Run against prod: `python3 aiem_chat_demo.py https://your.app.url`
