---
name: Layer 9 battery 100% failure — positional arg bug
description: root cause of Layer 9 statistical-edge scores failing on every ticker in production; a single-line fix in batch_layer9_scores
---

`batch_layer9_scores()` in `layer9_statistical_edge.py` calls
`pool.submit(compute_layer9_score, t, df, _chain_map.get(t))` — but
`compute_layer9_score`'s signature is `(ticker, history_df, lookback=60, chain_df=None)`.
The 3rd positional arg binds to `lookback`, not `chain_df`. This silently
overwrites `lookback` with either `None` (ticker has no options chain) or a
real `chain_df` DataFrame (ticker has a chain), both of which blow up at
`lk = min(lookback, len(close) - 1)`:
- `lookback=None` → `TypeError: '<' not supported between instances of 'int' and 'NoneType'`
- `lookback=<DataFrame>` → `ValueError: The truth value of a DataFrame is ambiguous...`

**Why this matters:** both call sites of `batch_layer9_scores()` in main.py
(the AI-trades layer9 enrichment at ~L48009 and the layer9_bg scan at
~L54120) are affected — this is a systemic 100% failure of the entire
Layer 9 sub-score battery, not a per-ticker data issue. Confirmed live via
DB query (2026-07-10: 113/113 rows in `layer9_scores` had non-null `error`,
split ~80/33 matching the two branches) and via a captured real traceback
from an instrumented copy of the module pointing at the exact `min()` line.

**How to apply:** the fix is to call with a keyword arg:
`pool.submit(compute_layer9_score, t, df, chain_df=_chain_map.get(t))`.
Direct calls to `compute_layer9_score(ticker, df)` elsewhere in main.py
(2-arg form) are NOT affected — only the 3-positional-arg call inside
`batch_layer9_scores` is broken. A separate, independently-confirmed bug
also affects this battery: main.py's `_run_layer9_bg_scan()` writer reads
`_comps.get("vrp", {})` but the real component key is `"vrp_proxy"`,
so `vrp_score` is always written NULL regardless of the above fix.
