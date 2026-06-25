---
name: Flow Streak ignition signal
description: Backtest-derived ignition filter added to Accumulation Streak tab — ASTE/AMLX two-week move analysis
---

# Rule
A stock's streak qualifies as "ignition" when ANY streak day satisfies all three:
- rvol ≥ 1.5× (volume spike vs 20-day rolling baseline)
- day gain ≥ 3.0% (open-to-close)
- close position ≥ 0.70 (closed in top 30% of the day's high-low range)

# Why
Backtest on ASTE (+24.8% mo) and AMLX (+32.5% mo): both had exactly this signature on the first/second day of their sustained streak. The false positive (AMLX Jun 4 +8.0% spike) failed the streak≥2 gate — it reversed -6.5% the next day before a second ignition day could form.

# How to apply
Backend: `has_ignition`, `max_rvol`, `max_day_pct` added to `_compute` in `_run_multiday_flow_scan`.
Frontend: "⚡ Ignition Signal" toggle chip in NetFlowStreakTab. Cards with ignition get an orange border + rvol/day% badge.
Scan result (first run after deploy): 71/851 stocks flagged as ignition.

# Thresholds
- rvol ≥ 1.5× — not 1.2×, which fires on normal accumulation
- day ≥ 3% — not 2%, which catches too many low-volatility stocks
- close_pos ≥ 0.70 — rules out intraday fades (buy turned sell-into-close)
- streak ≥ 2 is handled implicitly: a one-day blow-off collapses the streak before the next ignition day can form

# Do not lower thresholds without re-backtesting
Lowering any threshold materially increases the false-positive rate. The Jun 4 AMLX false spike was rvol=2.6× and +8%, but still correctly filtered out because streak collapsed to 0 before day 2.
