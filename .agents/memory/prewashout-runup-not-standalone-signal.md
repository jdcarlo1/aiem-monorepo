---
name: Pre-washout run-up analysis (Washout Ignition precursor)
description: Event-study of the rally that precedes a Washout Ignition trough — why it isn't a standalone tradeable entry signal
---

## Finding
For confirmed Washout Ignition fires, there IS a real prior rally: median +15.4% over ~21 trading days (~4.3wk), peaking at median RSI 64, followed by a ~18-day (~3.6wk) grinding sell-off into the oversold trough. But run-up size/speed/RSI-at-peak only predicts the *quality* of the eventual Washout Ignition trade (big/slow/moderately-overbought rallies → materially better win rate and fwd return) — it does not work as its own earlier entry.

The smallest/fastest rallies (the ones easiest to catch early, e.g. a 1-day spike) are the WORST performers (35-40% win rate, negative fwd 20d), while genuine multi-week grinds are the best (~70%+ win rate). Since you can't know in advance which small rally will develop into the good kind, "buy the rally, exit before washout, re-enter at Washout Ignition" has no standalone edge in this dataset.

**Why:** User's stated decision (2026-07-02) was to NOT build this as a separate precursor signal and stick with Washout Ignition as-is, given this result.

**How to apply:** If asked again to revisit "catch the run-up before the washout," this has already been tested and rejected — the value is as a *confidence filter* on the existing Washout Ignition confirm (retrospectively check pre-trough rally quality to size conviction), not a new entry point. Peak-detection methodology note: naive "max close in fixed N-day lookback" doesn't converge (finds arbitrary ATH from unrelated earlier swings) — must use a support-revisit method (walk backward from trough to the most recent point where close <= trough_close) to isolate the single relevant cycle.
