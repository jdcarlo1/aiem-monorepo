---
name: Risk gate / paper-execution enforcement gaps
description: Non-obvious ways pre_decision_risk_gate.py and the paper-trading engine can fail open rather than fail closed; found via full-repo audit 2026-07-01.
---

- `run_risk_gate()`'s critical checks are only as good as what the CALLER supplies — the gate does not call `market_regime_overlay.py` itself, it trusts a `market_regime_result` JSON blob passed in by the caller (an LLM agent). Missing/omitted input degrades to a soft "proceed with caution" reason, never a block.
  **Why:** confirmed by reading `run_risk_gate()` — `if market_regime_result is None: reasons.append(...)` (non-blocking) vs `elif ...get("recommendation")=="sit_out": blocking_reasons.append(...)`.
  **How to apply:** don't assume `run_risk_gate` independently re-derives regime/vote state; any audit of "is the gate real" must check whether the upstream producer of that JSON is actually wired into the same call path, not just that the gate has a branch for it.

- `risk_gate_passed` is COSMETIC in `_aiem_tool_send_discovery_alert` (main.py) — it only changes the `gate_str` display text in the email HTML; `send_email_raw()` fires regardless of its value. There is no deterministic code that blocks sending when the gate says BLOCKED — enforcement is entirely LLM-agent discretion (the agent is instructed via docstring/system prompt to not send if blocked, but nothing in code stops it).

- `position_reconciler.reconcile_positions()` is never called anywhere except its own `__main__` test block. `reconciliation_log` is therefore never populated in production, so the risk gate's `has_unresolved_mismatch()` check always returns False (0 rows) — a permanent silent pass, not because it was intentionally disconnected (unlike historical_analog_search.py/microstructure_proxy.py, which are explicitly documented as unwired) but because nothing schedules the producer.

- `daily_loss_limit.get_account_value()` and the paper-portfolio endpoint's `_account_start=20000.0` are both hardcoded/env-placeholder capital figures, not live account equity — the % daily-loss breach calc is only as accurate as this static number.

- Paper mark-to-market (`_aiem_paper_mark_to_market` in main.py) asks GPT to decide HOLD/EXIT per position with **no deterministic stop-loss** other than a 14-day time cap. If the LLM call throws OR returns malformed JSON, `_ai_decisions` stays `{}` and every open position defaults to HOLD that day — an LLM outage means zero risk management (beyond the 14-day cap) until the next successful MTM run.

- "CALL_OPTION" paper trades are not real option pricing: P&L is a synthetic `underlying_move_pct * 2.0` proxy (floored at -100%), no strike/expiry/theta/IV modeled. Track record for that trade_type diverges from real options economics by construction.
