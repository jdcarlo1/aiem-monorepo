---
name: AIEM Independent Picks (Workstream D) is the "AIEM's own intelligence" system
description: Which of the 3 AIEM pick-generating systems actually matches "no pre-scored recommendations, AIEM decides itself" - and how it's wired to Telegram.
---

## Three distinct AIEM pick-generating systems - do not conflate them

1. **Legacy morning scan** (`_run_aiem_morning_scan`, `aiem_predictions` table, 9:05 AM ET) -
   hands AIEM pre-computed composite/conviction scores from the website's own scanners.
   AIEM only re-ranks/selects from an already-scored shortlist.
2. **Probability Engine** (`artifacts/stock-scanner-api/aiem_probability_engine/`, 10:30 AM ET) -
   pure statistical/ML re-ranking of `ai_short_calls_log` candidates. NOT AI reasoning at
   all - no LLM involved. See `aiem-autonomous-scan-mode-naming.md` for the user's name for
   a possible future full-market-independent evolution of *this* engine (not yet built).
3. **AIEM Independent Picks / "Workstream D"** (`_run_aiem_independent_scan`, gpt-5.4,
   9:20 AM ET, table `aiem_independent_picks`) - the ONLY system where AIEM gets **raw
   Polygon data with zero conviction/composite score** and must reason to its own picks
   independently. Tools: `get_raw_stock_universe` / `get_raw_options_universe` (both
   explicitly documented as score-free) / `save_independent_picks`.

**Why this matters:** when a user asks for "AIEM's own intelligence, not OpenAI-given
recommendations," system #3 is what they mean - not #1 (pre-scored) and not #2 (not AI).
This took multiple rounds of requirement clarification to pin down; don't rediscover it -
go straight to Workstream D.

## Current wiring (as of 2026-07-01)

- Combined cap is 30 picks TOTAL across stock + call_option together (not 20+20=40).
  Enforced server-side in `_aiem_indep_tool_save_independent_picks` by sorting all
  candidates by `confidence_score` across both types and truncating - this holds
  regardless of what the LLM actually returns, so the LLM-side prompt wording is a
  secondary guardrail, not the only one.
- `aiem_telegram_notifier.py` sends this list to Telegram at 9:30 AM ET (10-min buffer
  after the 9:20 AM scan, matching the codebase's usual write→read buffer convention).
  This REPLACED the old 9:15 AM brief that read `aiem_predictions` (system #1) - the user
  explicitly did not want two separate/conflicting daily messages.
- Message is chunked defensively if it would exceed Telegram's 4096-char limit.
