---
name: AIEM tool map canonical source
description: _build_aiem_tool_map() is the single source of truth for all AIEM sessions; how to keep it in sync with new tools
---

# AIEM Tool Map

## Rule
`_build_aiem_tool_map()` in main.py is the canonical merged map for ALL AIEM session sources:
- `_run_aiem_focused_session()` (email/SMS/chat tab)
- The web chat tab worker (`aiem_chat_start`)

The research agent (`_run_aiem_research_agent`) has its own inline `_tool_map` — they must stay in sync.

## What happened
Prior `_build_aiem_tool_map()` had only 64 entries. The AIEM schema (`_AIEM_AGENT_TOOLS`) advertised 135 tools. Any tool the model called that wasn't in the map returned `{"error": "unknown tool"}` silently — sessions appeared to complete but returned no useful findings for complex multi-tool questions.

**Why:** `_build_aiem_tool_map()` was extracted from `_run_aiem_focused_session` as a refactor but was never kept in sync when new tools were added to `_run_aiem_research_agent._tool_map`.

## How to apply
When adding a new AIEM tool (function + schema entry):
1. Add the function to `_build_aiem_tool_map()` (used by focused sessions + chat)
2. Add to `_run_aiem_research_agent._tool_map` (used by Sunday research agent)
3. Add to `_build_market_tool_map()` (separate dict, also needs the new function)
4. Add to `_AIEM_AGENT_TOOLS` schema list (what the model sees) — insert the new
   schema block immediately after an existing one that's early in the list
   (e.g. right after `mkt_compute_indicators`), NOT appended at the end.
All four must stay in sync or the model will call tools that silently 404.

## Schema truncation is not just one slice
`_AIEM_AGENT_TOOLS[:128]` (OpenAI's 128-tool hard cap) is applied at **3 separate
call sites** in main.py, not one shared variable — grep for `[:128]` to find them
all. They all slice the same underlying list object, so a new schema entry only
needs to land before index 128 once; verify with a quick Python index-count
script rather than trusting line-number guesses after edits shift things.

## Session lock architecture
All AIEM session entry points share ONE lock: `app._aiem_qa_lock` (threading.Semaphore(1)):
- Email/cron sessions: acquire before session, release in finally
- SMS webhook: acquire inside `_research()` thread, release in finally
- Chat tab worker: acquire at worker start, release in finally
- HTTP handler fast-checks lock non-blocking → 429 if busy; worker re-acquires

## Startup ordering
`reconcile_orphaned_sessions()` is defined at ~line 41716 but needs to run at startup (~line 3669).
Fix: `threading.Timer(3.0, lambda: globals().get("reconcile_orphaned_sessions", lambda: None)()).start()`
The 3s delay ensures the full module loads before the call.
