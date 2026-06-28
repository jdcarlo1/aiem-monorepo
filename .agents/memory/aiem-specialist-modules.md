---
name: AIEM specialist module wiring
description: How the 6 pre-existing specialist modules are wired into the paper trading engine and AIEM research loop
---

## Modules and their integration points

| Module | Where wired | Effect |
|---|---|---|
| `fred_macro` | Start of `_aiem_paper_pick_candidates()` | Risk-off (sum≤-2) caps picks at 10, de-weights CALL_OPTIONs 60% |
| `social_sentiment` | After signal scoring in `_aiem_paper_pick_candidates()` | StockTwits bullish_pct ≥0.72 + tagged≥4 → score ×1.15; ≤0.30 → ×0.85 |
| `specialist_council` | After sentiment in `_aiem_paper_pick_candidates()` | `compute_weighted_verdict([signal_engine, fred_macro])` → score ×(1 ± 0.20×council_vote) |
| `bull_bear_debate` | `_aiem_paper_execute_today()` for top 3 picks | NO_EDGE skips pick entirely; BEARISH_LEAN halves notional to $500 |
| `drift_alarm` | `_aiem_paper_drift_check()` at 4:35 PM ET scheduler | Compares paper win rates vs backtest baselines per signal source; alerts on ≥10pp gap |
| `active_hypothesis_selection` | AIEM tool `rank_hypothesis_candidates` | Ranks candidates by novelty × Thompson-sampled category value before register_hypotheses |

## Key design rules
- All 6 modules imported with try/except fallback at main.py line 38 — if import fails, the pick engine still runs (modules are additive, never blocking)
- `compute_weighted_verdict` (not `negotiate`) used for specialist_council to avoid LLM calls in the pick engine
- bull_bear_debate only runs for top 3 picks to control latency (runs in execute thread, not web request)
- drift_alarm needs ≥15 closed trades per signal source before reporting — expect silence for first 2-3 weeks

**Why:** bull_bear_debate makes 2 LLM calls (GPT bull + Claude bear) per ticker; running it on all 20 picks would cost ~$0.40/day and add 2+ min latency. Top 3 is the right balance.

## Scheduler job added
- `aiem_paper_drift` at 4:35 PM ET Mon-Fri (after 4:00 PM MTM has closed positions)

## AIEM tool schema
- `rank_hypothesis_candidates` added to both dispatch dict (~line 24581) and tool schema (~line 25056) in the focused-session tool list
