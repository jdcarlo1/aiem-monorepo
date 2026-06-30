---
name: precursor_signals event-study backtest results (2023-07 to 2025-06)
description: statistical findings from the full-market event-study backtest validating which precursor features actually precede >=15%/5d moves
---

Ran `event_study_backtest.py` full-market (4,699 tickers, 2023-07-01 to
2025-06-30, move threshold 15% within 5 trading days) via
`artifacts/stock-scanner-api/event_study_results.csv`. 65,635 qualifying
events vs. 1,948 random control samples, precursor window = trailing 10
trading days before the event.

**Strongest validated precursor (by effect size, not just p-value):**
`price_range_5d` (5-day high-low range as % of price) — both `_latest` and
`_mean_10d` versions had the largest effect sizes (~0.69-0.72) of any feature
tested. Stocks that go on to make a >=15%/5d move already have ~3x wider
5-day price range *before* the move starts (event_mean 0.30-0.31 vs.
control_mean 0.09-0.10) than a random stock. This is a **volatility/range
expansion continuation** signal, not a quiet-accumulation signal.

**Counter-intuitive finding:** `stealth_score` (the "quiet accumulation"
feature in `precursor_signals.py`) is *lower*, not higher, before real big
moves (event_mean ~0.027 vs. control_mean ~0.069-0.070, p<1e-11). The
stealth-accumulation hypothesis — that big movers are preceded by quiet,
low-volatility accumulation — was NOT supported by this full-market sample;
if anything the opposite pattern (already-expanding range) is what precedes
real breakouts. Treat `stealth_score` as a feature to validate per-regime
before relying on it, not a confirmed precursor.

**Secondary, smaller but significant effects:** `rsi_14` modestly lower
before events (47.5 vs 50.8, small effect size ~0.10) and trending down
(`rsi_14_slope` event_mean -0.18 vs control +0.09) — consistent with a mild
oversold-bounce component layered on top of the dominant range-expansion
signal. `rvol_trend_5d`, `pocket_pivot`, `squeeze_streak` had statistically
significant but small effect sizes (<0.04) — likely real but weak on their
own; not reliable as standalone filters given the huge n inflates
significance for tiny effects.

**Caveat on interpretation:** with n=65,635 events, p-values are significant
for almost any non-zero effect — always read the `effect_size` column, not
just `significant_at_0.01`, to judge whether a feature is actually useful.
