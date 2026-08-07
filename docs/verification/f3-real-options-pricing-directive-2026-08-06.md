# Directive_F3_RealOptionsPricing_2026-08-06 — results

## Full-scope
- `f3_strategy.py` / `spy_stoploss_sweep.py` were absent from monorepo at start.
- Synthetic `atm_est` / `leverage=clamp` formula: **not present** in repo strategy code.
- Real pricing implemented in `tools/f3_strategy.py` (Polygon 1-min option aggs).

## Evidence
```
$ grep -c 'options/SPY\|options.*aggs\|/v2/aggs/ticker/{option' tools/f3_strategy.py
5
$ sha256sum tools/f3_strategy.py
30bb594f9bcc21d5a8028a2725d4049525e25272dac8b40bb460589f471add9b  tools/f3_strategy.py
```

## 1y re-run (2025-08-06 → 2026-08-06) — REAL Polygon option bars

### A) No stop (directive default: sell 16:00)
- Trades: **178**
- Skipped (no quote): **0** (0.0% of signals)
- Win rate: **43.3%**
- Notional: $35,600
- P&L: **+$11,552.97**
- ROC: **+32.5%**
- Thin exits (n_tx≤3): 22 / 178 = **12.4%**
- Artifact: `artifacts/backtests/f3_real_options_trades_nostop.csv`

### B) With −65% premium stop (live-paper rule)
- Trades: **178**, skipped **0**
- Win rate: **38.8%**
- P&L: **+$13,717.21**
- ROC: **+38.5%**
- Artifact: `artifacts/backtests/f3_real_run_with_65pct_stop.txt`

### vs prior synthetic
- Synthetic (reported): +$7,070 / 19.9% ROC
- Real no-stop: +$11,553 / 32.5% ROC (aligns with prior ~+$11,899 verification note)

## UI wiring
Pattern Lab / OE Strategies poll live `/pattern-lab/snapshot` → in-memory `aim_f3_spy_0dte`. They do **not** read this CSV/backtest output.
