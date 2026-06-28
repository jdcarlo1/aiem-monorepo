---
name: quant_agent_sessions auto-create
description: The quant_agent_sessions DB table has no dedicated CREATE TABLE; must be created at startup
---

## Rule
`quant_agent_sessions` is referenced throughout the Quant Agent (chat) system but has no standalone
`CREATE TABLE IF NOT EXISTS` call. If the prod DB doesn't have this table (fresh deploy), all chat
requests return HTTP 500 → `res.json()` may throw → frontend catch fires: "Failed to start session".

**Fix applied:** `reconcile_orphaned_sessions()` (runs 3s after startup) now does
`CREATE TABLE IF NOT EXISTS quant_agent_sessions (...)` before the UPDATE orphaned-rows step.
This means a clean prod environment gets the table automatically on first restart after deploy.

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS quant_agent_sessions (
    job_id      TEXT PRIMARY KEY,
    question    TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    answer      TEXT,
    error       TEXT,
    current_tool TEXT,
    tool_trace  JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```
