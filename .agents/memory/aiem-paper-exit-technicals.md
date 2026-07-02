---
name: AIEM paper-trading exit now uses full technical stack
description: 4PM mark-to-market exit judgment wired to same indicator/specialist stack as entry; token-budget gotcha for per-item JSON array LLM calls
---

## What changed
`_aiem_paper_mark_to_market()` (4PM hold/exit decision) previously reasoned from
raw OHLCV/vwap/rvol/gap_pct/close_strength only. It now also computes, per open
position, the same stack already used at entry: `_mkt_compute_indicators()`
(RSI-14, Stoch %K, CMF-20 + signal, MACD hist, ADX-14, overall buy/sell/neutral),
`_social_sentiment` bullish %, `_fred_macro` risk-on/off bias, and a
`_specialist_council` weighted verdict (-1 exit-leaning to +1 hold-leaning).
These are added to each position's JSON block and the GPT exit prompt explains
how to weigh them alongside price action.

Field names from `_mkt_compute_indicators()` return value: the indicator values
live under `result["snapshot"]` (e.g. `snapshot["rsi_14"]`, NOT `rsi14`), and
`signal_summary` is nested *inside* `snapshot`, not a top-level key of the
return dict. Easy to get wrong — always re-check field names against the
function source, don't assume.

## Token-budget gotcha (generalizable lesson)
Any GPT call that returns **one JSON array with one object per input item**
(e.g. "decide HOLD/EXIT for each of N positions") needs its
`max_completion_tokens` to scale with N, or larger cohorts silently get a
mid-array truncated/invalid JSON response — the code falls back to
"decision call failed, held on price-only fallback" for every single item,
silently defeating the feature for that cycle with no crash, just a
misleading blanket fallback.

**Why:** a hardcoded `max_completion_tokens=2000` had worked fine when the
open-position count was low, but broke 100% of decisions (`Unterminated
string` JSON error) the moment the book grew to 44 positions, especially
after adding more context fields per position (longer prompt does NOT bound
output, but richer context does tend to make the model write longer per-item
reasoning, compounding the problem).

**How to apply:** for any "N-items-in, N-decisions-out JSON array" LLM call,
compute the token budget from the item count, e.g.
`max(min_floor, n_items * per_item_estimate + fixed_overhead)`, and instruct
the model to keep any free-text per-item field short (e.g. "<= 12 words") so
the per-item cost estimate stays valid as the cohort grows.
