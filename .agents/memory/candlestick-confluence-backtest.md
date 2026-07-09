---
name: Candlestick-confluence real statistical backtest
description: How the candlestick-confluence scanner's patterns were backtested for real, and what it found
---

Backtest is a new AIEM tool `mkt_backtest_candlestick_confluence` (Category D
adapter pattern — a Python function doing pandas-level pattern detection
across a pulled multi-ticker history, not expressible as flat SQL like
`mkt_test_signal`). Main agent only wrote the tool; AIEM calls it and
interprets results, per the backtest-delegation-rule.

Methodology: ports the user's original `run_backtest()`/`summarize()` script
1:1 (11 patterns x 4 confluence levels [base/+vol/+support/+vol+support+rsi<40]
x horizons [1,3,5,10]d), but pooled across a real multi-ticker universe from
`polygon_market_daily` (ranked by dollar volume, capped ~2500 tickers) instead
of one ticker. Adds real significance testing on top of the original script:
Welch's t-test per combo vs the full-universe baseline at that horizon, with a
Bonferroni-corrected alpha (many combos tested at once) and an n>=200
reliability floor before a result counts as `best_signal` — same n>=200
convention used elsewhere in this codebase (see aiem-indicator-grid-battery.md).

**Why:** win-rate/avg-return alone (what the reference script produced) isn't
statistically rigorous without a baseline comparison + p-value; this codebase's
own convention (Bonferroni ledger, mkt_test_signal) requires that bar.

**Live finding (2026-07-09, 2yr window, ~2500 tickers):** `bullish_marubozu`
+ full confluence (+vol+support+RSI<40) at 10d horizon was the only combo to
clear n>=200 AND Bonferroni-significance: 59.6% win rate vs 53.2% baseline
(+6.4pp edge, p=0.00003, n=213) — but its **average return was -0.7% vs +1.12%
baseline**, i.e. it wins more often with a worse average payoff. Treat as an
in-sample historical association, not a validated live trading edge (no
walk-forward split, survivorship bias from picking the universe by current
liquidity, no transaction costs modeled — the tool reports these caveats
itself every run). Admin verification endpoint:
`/stock-api/admin/backtest-candlestick-confluence` (ADMIN_TOKEN-gated).

**Indicator-combo search tool (2026-07-09):** added a second AIEM tool,
`mkt_test_candlestick_indicator_combo`, purpose-built for cheaply iterating
many indicator filters around ONE pattern (user wanted to try swapping/adding
indicators around bullish_marubozu after the trap above). It caches the
built OHLCV+pattern+indicator universe (joined from `polygon_indicators_daily`:
rsi_14/stoch/macd/adx_14/cmf_20/mfi_14/cci_20/williams_r/bb_pct/roc_12/
momentum_10/atr_pct/pct_from_sma20-50-200/pct_from_52w_high-low) keyed by
(start,end,min_price,min_volume,max_tickers) for 15 min, so the first call is
slow (~60-90s cold-build) and every later call with the SAME window params is
near-instant — this let one AIEM session run 20+ combo tests in ~210s. Returns
`genuinely_improved` = only true when n>=200, p<0.05, AND both win-rate edge
and avg-return edge are positive (guards against the same win-more-pay-less
trap). Admin verification endpoint:
`/stock-api/admin/test-candlestick-indicator-combo` (ADMIN_TOKEN-gated).

**Finding:** none of the money-flow (CMF/MFI) or trend-alignment (SMA20/50)
filters cleared the bar. The one that did: requiring the stock be **at least
5-10% below its 52-week high** (`pct_from_52w_high_max: -5` or `-10`) at 5d/10d
horizons — genuinely_improved=true, independently re-verified against the raw
tool output: 5d n=667 57.0% WR (+3.7pp) avg_ret +1.66% (+1.06pp) p=0.0024;
10d n=650 55.7% WR (+1.8pp) avg_ret +2.46% (+1.34pp) p=0.0229; 10d @ -10%
n=462 56.3% WR (+2.4pp) avg_ret +3.02% (+1.89pp) p=0.0156. Interpretation:
marubozu works better as a recovery/continuation signal off a depressed base,
not as a breakout-near-highs signal. Still in-sample/no walk-forward — same
caveats as above apply.
