---
name: Pre-move technical signature lead time is variable, not a fixed 2 weeks
description: Findings from validating the CMF/RSI washout-to-accumulation precursor pattern against a fresh batch of explosive movers
---

Validated the washout-then-reversal signal (CMF flips negative→positive + RSI recovers from oversold) against a new out-of-sample batch of ~12-13 explosive movers (FLXS, KRT, QLYS, MAX, BKTI, NUTX, ATEX, ELF, CXT, PESI, BLZE, RGNX), on top of the original 7-ticker case study (ORIC/MBX/AGYS/GPGI/GSHD/MPLT/ASTH).

Finding: a strict fixed 15-trading-day (~3-week) lookback did NOT catch any of the 12 new tickers, because most had already begun their run more than 3 weeks before the reference date. Locating each ticker's own actual CMF trough (accumulation low point) showed lead times ranging 14-44 trading days (~2-9 weeks) before the observed price level, with a cluster around 4-6 weeks, not a fixed 2 weeks.

**Why:** Users kept asking to "validate the 2-week lead time," but real accumulation phases vary in duration per name/sector/float — there is no universal countdown clock. The full-market backtest (n=19,239 signal-days) already quantified this as a modest, statistically real but not strongly predictive edge (+1.5pp on ≥20% moves), consistent with high per-ticker lead-time variance.

**How to apply:** When asked to validate lead time on new tickers, don't force a single fixed-day lag — find each ticker's own indicator trough within a rolling window (e.g. trailing 45 trading days) and report the actual distribution of lead times, rather than reporting pass/fail against one hardcoded day-count.
