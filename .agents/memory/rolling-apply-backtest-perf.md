---
name: pandas rolling().apply() cost at per-event backtest scale
description: why per-event/per-row feature extraction in event-study-style backtests must avoid Series.rolling().apply(), even on tiny windows
---

`Series.rolling(window).apply(callback, raw=False)` recomputes the callback at
**every position** in the series, not just the position you actually want. If
you only need the slope (or any rolling stat) at the *last* index of a small
window — which is the common case when summarizing a trailing precursor
window per event in a backtest — calling `.rolling().apply().iloc[-1]` throws
away O(window) of wasted work, and that overhead is paid by `Series.rolling`
machinery + Python callback dispatch, not the math itself.

**Why:** A per-event feature-extraction function in `event_study_backtest.py`
profiled at ~3.3ms/call with 8 `rolling().apply()` calls inside it (one per
feature column). Replacing each with a one-shot numpy OLS-slope computed
directly on the last N values (no pandas rolling object at all) dropped that
to ~0.70ms/call — a 4.7x speedup — with zero change to the math or test
results (18/18 checks still pass). At ~70K calls (events + control sample)
in a full-market multi-year run, this was the difference between the
extraction step finishing in ~48s vs. ~227s, which is what made a single-pass
backtest run (instead of needing 3+ resumed/checkpointed calls) feasible.

**How to apply:** Any time you see `<rolling_or_expanding>.apply(...).iloc[-1]`
(or `.values[-1]`) inside a function that runs per-row/per-event at backtest
scale (thousands+ calls), replace it with a direct one-shot computation over
just the trailing slice you need. Keep the original rolling-Series version
only for call sites that actually need the full rolling Series (e.g. live
scanning in `precursor_signals.py`, which is the canonical `rolling_slope`).
