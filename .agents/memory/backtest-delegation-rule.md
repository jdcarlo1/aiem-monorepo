---
name: Backtest delegation rule
description: All backtesting and historical data analysis must be routed to AIEM, not run by the main agent
---

## Rule
When the user asks for any backtest, historical win rate calculation, signal analysis, or data-driven research query against the scanner's database tables, route it to AIEM — do not run it yourself.

**Why:** The user pays extra when the main agent runs analysis that AIEM can do for free using its built-in DB tools. This was explicitly confirmed by the user on 2026-07-06.

## How to apply
- If the user says "backtest this," "what's the win rate of X," "run the numbers on Y," "have AIM/AIEM work on this," or similar — **do not** take the analysis as your own continuous job.
- Correct path (see also `aiem-communication-protocol.md`):
  1. Write/stand up a script under `artifacts/stock-scanner-api/` for AIEM to run, **or**
  2. For ongoing/24h work: bake into `aiem-process` / a workflow (code edit) and restart that workflow — the restart is the handoff.
- The main agent may do surgical code wiring + verify logs/DB; AIEM (or its script/workflow) executes the research.
- Exception: if AIEM is down, broken, or explicitly unavailable, the main agent may run analysis as a fallback — note that to the user.

## Scope
Applies to any query against: `polygon_market_daily`, `polygon_rvol_scan`, `aiem_process_predictions`, `aiem_process_outcomes`, `aiem_signal_discoveries`, `ai_short_calls_log`, or any other scanner/backtest table.
