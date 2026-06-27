---
name: Quant Agent Chat Tab
description: How the AIEM chat tab is wired — DB table, 3 Flask endpoints, React component
---

## Quant Agent Chat — Architecture

### DB table: `quant_agent_sessions`
- `job_id` TEXT PK, `question` TEXT, `status` TEXT (pending→running→done|error), `answer` TEXT, `error` TEXT, `created_at/updated_at` TIMESTAMPTZ

### Flask endpoints (in main.py, end of file before `if __name__`)
- `POST /stock-api/aiem/chat` — takes `{question}`, saves to DB, spawns daemon thread calling `_run_aiem_focused_session(max_iterations=3)`, returns `{job_id}` immediately
- `GET /stock-api/aiem/chat/<job_id>` — poll for result
- `GET /stock-api/aiem/chat/history` — last 20 sessions

**Why `max_iterations=3`:** Each AIEM iteration can call multiple tools; 3 iterations = ~2-4 min response time. Increasing to 5+ risks 8-10 min waits.

### React component: `QuantAgentTab` in Dashboard.tsx
- Defined before `NetFlowTab`, around line 11840
- Polls every 3s, elapsed timer counts up, 5 example question chips shown when no history
- `BASE = import.meta.env.BASE_URL.replace(/\/$/, "")` for all API calls

### How to apply
- Adding new chat features: extend the prompt builder in `aiem_chat_start()` to route question types to specific tools
- Streaming: would require SSE (`text/event-stream`) — not implemented yet; polling is current approach
- Rate limiting: currently no concurrency limit on chat sessions — each is a daemon thread

**Why:** Owner wanted to replace email-based Q&A with a real-time chat interface using the AIEM engine that already powers the research agent.
