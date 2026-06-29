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

## Instrumentation added

`t_tool_s` and `t_llm_s` are now recorded in every trace step inside `_run_aiem_focused_session`. Poll endpoint exposes `tool_trace` with these fields. Session total logged on completion.

## Optimizations applied

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
