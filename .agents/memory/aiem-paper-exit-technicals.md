---
name: AIEM paper-trading exit — rules-based, no OpenAI
description: 4PM MTM exit judgment is now a pure indicator rules engine; no LLM call; architectural decision that AIEM owns all exit decisions itself
---

## Current state
`_aiem_paper_mark_to_market()` (4PM hold/exit decision) collects the full
indicator stack per position — RSI-14, MACD hist, CMF-20 + signal,
close_strength, specialist_council_score, macro_bias (FRED), social sentiment
bullish % — then runs each position through `_rules_mtm_decision()`, a local
pure-Python rules function. **No OpenAI call.** AIEM decides from its own data.

## Rules engine logic (`_rules_mtm_decision`)
Exit evidence (any count, exit wins if ≥2 and exit_ev > hold_ev):
- RSI ≥ 72 → overbought
- MACD hist < 0 → momentum fading
- CMF signal "distribution" or cmf_20 < -0.10 → money flowing out
- close_strength ≤ 0.35 → closed near lows
- specialist_council_score ≤ -0.30 → council bearish
- overall_signal == "sell" → full suite says sell
- RISK-OFF macro + pnl > 3% → lock in gains
- rvol < 0.75 after 5+ days → volume fading

Hold evidence mirrors the above with bullish thresholds.
Verdict: EXIT if ≥2 exit signals AND exit_ev > hold_ev; else HOLD.

## Intraday (9:35–16:00, every 30 min)
`aiem_exit_engine.review_open_positions()` runs separately — already
pure-rules (RSI/MACD/SMA20/lower-lows), never used OpenAI.

## Why
User explicitly stated AIEM should make exit decisions from its own data
and tools, not by outsourcing reasoning to an LLM. All the needed
information (RSI, MACD, CMF, close_strength, council, macro) is already
fetched; routing it through GPT added latency, cost, and dependency on an
external service for what is a deterministic judgment call.

## Field-name gotcha
`_mkt_compute_indicators()` returns `result["snapshot"]["rsi_14"]` etc.
`signal_summary` is nested inside `snapshot`, not top-level. Easy to get wrong.

## Token-budget gotcha (archived, now moot)
The old LLM path needed `max(4000, n * 120 + 1000)` tokens or JSON was
truncated mid-array at large position counts. No longer relevant.
