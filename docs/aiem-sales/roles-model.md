# AIEM Terminal — Roles Model

Commercial packaging roles. **Today:** any valid admin token acts as Admin.  
**Target:** enforce these roles in auth middleware.

| Role | Can do | Cannot do |
|---|---|---|
| **Viewer** | Read Command Center, paper book, Sales Readiness, performance | Force execute / MTM / admin mutations |
| **Trader** | Viewer + paper force-execute / force-MTM | Change auth, kill switches, schema |
| **Auditor** | Viewer + evidence/audit/diligence export | Place paper trades |
| **Admin** | Full token surface | — |

UI shows the active role in the sidebar footer (`aiem_role` session key).
Default on login: **Admin** (matches current single-operator reality).
