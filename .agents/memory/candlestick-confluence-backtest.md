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
