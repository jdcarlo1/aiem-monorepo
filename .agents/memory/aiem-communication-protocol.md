---
name: AIEM communication protocol (Replit loop)
description: How the assistant talks to AIEM — no inbox; code/DB/endpoints/scripts only. Confirmed by Joel via stocksai.com screenshots 2026-08-05.
---

# AIEM has no chat inbox

AIEM does not receive instructions at runtime. You communicate by changing its **codebase (brain)** or **database (memory)**, then verifying. The assistant is the surgeon; restart / next poll / curl is the message.

## Full loop

1. User gives a task  
2. Read relevant code / current state  
3. Edit code / write script / run SQL / curl admin  
4. Branch:  
   - **Code changed** → restart the owning workflow; watch logs until alive  
   - **DB changed** → AIEM picks it up on next poll (1–5 min); no restart  
   - **One-time task** → curl admin endpoint (or port-5055 trigger); read response  
5. Verify (logs, DB query, or health check)  
6. Report back to user  

## Step 1 — Classify the task

| Kind | Example | Mechanism |
|------|---------|-----------|
| Permanent behavior | "add a signal," "run discovery 24/7," "fix learning loop" | **Code edit + workflow restart** |
| One-time action | "run a scan now," "trigger premarket" | **Admin HTTP / trigger server** |
| Data / analysis | "backtest this," "research win rate" | **Delegate script to AIEM path** — never run backtests as the main agent’s own job |
| Config / flags | strategy registry, system_state, governance | **SQL** → next poll |

## Step 2A — Code change (most common)

1. Read: `artifacts/stock-scanner-api/main.py` (stock-api / AIEM brain), plus `aiem_process.py`, `aiem_telegram_notifier.py` as needed.  
2. Surgical edit.  
3. Restart the owning workflow only:  
   - `artifacts/stock-scanner: stock-api`  
   - `artifacts/stock-scanner: aiem-process`  
   - `artifacts/stock-scanner: aiem-telegram`  
4. Refresh logs; confirm new logic is alive.  

**The restart is the communication.** No “message” is sent to AIEM.

## Step 2B — One-time trigger

```bash
curl -s -X POST \
  http://localhost:PORT/stock-api/admin/run-scan \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

`aiem-process` also has a trigger server on **port 5055** (`/trigger/premarket`, `/trigger/grade-outcomes`, etc.) — same pattern: authenticated POST, immediate execution.

## Step 2C — Data / analysis (backtests, research) — STANDING RULE

**Never run backtests yourself as the Cursor/main agent’s continuous job.** Delegate to AIEM:

1. Write a standalone Python script under `artifacts/stock-scanner-api/`  
2. Run it via shell inside the AIEM/stock-scanner environment (Replit: `ShellExec`)  
3. Script connects to DB / Polygon, prints results  
4. Read console output and report to the user  

If the user says “have AIEM / AIM do X for 24 hours,” that is **2A permanent behavior** (bake into `aiem-process` or a dedicated workflow + restart) — **not** the Cursor agent looping the work.

## Step 2D — Database change

```text
executeSql({ sqlQuery: "...", environment: "development" })
```

AIEM picks up on next poll (typically 1–5 minutes). No restart required.

## Cursor Cloud note (2026-08-05)

Replit Agent tools (`WorkflowsRestart`, `RefreshAllLogs`, `ShellExec`, `executeSql`) are the native control plane on stocksai.com. From Cursor Cloud:

- **Same brain:** edit the same repo files (that is still how you “tell” AIEM).  
- **Restart / Publish:** may require Replit workflow restart or user Publish after merge — say so explicitly if this environment cannot restart Replit workflows.  
- Do **not** substitute by running AIEM’s 24/7 jobs yourself in the Cursor agent session.

## Anti-patterns

- Treating AIEM as a chat peer you “message”  
- Running multi-hour / 24/7 discovery or backtests in the Cursor agent instead of wiring AIEM  
- Editing code and claiming AIEM “knows” without restart (code) or poll (DB) or curl (one-shot)
