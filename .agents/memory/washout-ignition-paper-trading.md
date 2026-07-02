---
name: Washout Ignition Signal wired into AIEM paper trading
description: How the Washout Ignition Signal (RSI>=70 breakout) source was added to the AIEM Paper Money candidate picker
---

The AIEM paper trading candidate picker (`_aiem_paper_pick_candidates()` in
`artifacts/stock-scanner-api/main.py`) aggregates ~8 signal sources into one
ranked list before executing the daily 20 paper trades. Each source is a
self-contained `SELECT ... ; for row: _add(ticker, score, trade_type, source, detail)`
block inside one shared `try/except` — adding a new source is safe/additive
because a failure in one block only raises out of the whole `try` (logs
`[aiem_paper] pick error`), it does not silently corrupt other sources.

Washout Ignition Signal (`washout_ignition_signal` table, RSI>=70-at-breakout
filter) was added as source #8, scored as `breakout_pct*1.5 + vol_x*2.0 + 5.0`
(flat +5 base to reflect it's a rarer, higher-conviction validated setup vs.
noisier high-frequency sources like sweeps/gap-volume).

**Why:** user wanted every validated in-house signal to flow into the same
paper-trading feedback loop so its real forward performance gets tracked
automatically, without a separate bespoke pipeline per signal.

**How to apply:** to wire any *new* validated signal into paper trading, add
one more numbered block in `_aiem_paper_pick_candidates()` following the same
pattern (query the signal's own table, call `_add(...)`), no other file
changes needed — the frontend's Paper Money tab renders `signal_source` via a
generic `.replace(/_/g," ")` (no per-source frontend code required).
