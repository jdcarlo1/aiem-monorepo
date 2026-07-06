---
name: Backtest delegation rule
description: All backtesting and historical data analysis must be routed to AIEM, not run by the main agent
---

## Rule
When the user asks for any backtest, historical win rate calculation, signal analysis, or data-driven research query against the scanner's database tables, route it to AIEM — do not run it yourself.

**Why:** The user pays extra when the main agent runs analysis that AIEM can do for free using its built-in DB tools. This was explicitly confirmed by the user on 2026-07-06.

## How to apply
- If the user says "backtest this," "what's the win rate of X," "run the numbers on Y," or similar — tell them to ask AIEM directly in the chat, or explicitly say you are routing it there.
- The only work that stays with the main agent is the **file edit** at the end once the research is done and the user has decided what change to make.
- Exception: if AIEM is down, broken, or explicitly unavailable, the main agent may run the analysis as a fallback — but note this to the user.

## Scope
Applies to any query against: `polygon_market_daily`, `polygon_rvol_scan`, `aiem_process_predictions`, `aiem_process_outcomes`, `aiem_signal_discoveries`, `ai_short_calls_log`, or any other scanner/backtest table.
