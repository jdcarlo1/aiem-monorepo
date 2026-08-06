---
name: Orchestrator audit session closure fix
description: run_full_cycle() now closes its master_orchestrator audit session with stage count
---

## Problem (pre-fix)
`_h_performance_auditor()` opened an `aiem_research_audit_sessions` row (type=master_orchestrator)
via `self._pa._aeim_start_audit_session("master_orchestrator")`. But:
- `run_full_cycle()` never called `_aiem_close_audit_session()`
- `_run()` method never called `_aiem_log_tool_call()`
- `total_tool_calls = COUNT(aiem_research_tool_audit WHERE session_id=…)` = always 0
- Result: 79 sessions stuck open with `total_tool_calls=0, ended_at=NULL`

## Fix (2026-08-04)
Added a `try/except` block at the end of `run_full_cycle()` (after all stages, before `return packet`):

```python
_orch_sid = (packet.performance.get("auditor") or {}).get("audit_session_id")
if _orch_sid and _orch_sid != "NO-AUDIT":
    UPDATE aiem_research_audit_sessions
    SET ended_at=NOW(), total_tool_calls=len(packet.audit),
        verdict='Orchestrator pipeline complete — N stages executed.', strict_pass=TRUE
    WHERE session_id=_orch_sid AND ended_at IS NULL
```

SHA post-fix: c9e0409b148d714e41fa544c29b8065e4e680a0ec9dcdd2ca3d7f7b5838ec8fb

## Why total_tool_calls = len(packet.audit)
The orchestrator runs deterministic Python stages, not LLM tool calls. `len(packet.audit)` is the
real pipeline stage count. Using `COUNT(aiem_research_tool_audit rows)` would always be 0 because
the orchestrator never writes there — the SQL `total_tool_calls = (SELECT COUNT(*) FROM ...)` in
`_aiem_close_audit_session` was overridden with a direct SET instead.

## Important: pre-existing sessions
The 79 sessions from before the fix still have `ended_at=NULL`. They cannot be retroactively closed
(the audit data is incomplete). Only new sessions from D2 cycles after this fix will close properly.
