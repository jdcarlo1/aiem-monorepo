# Gamma Blast — Polygon capability + AIEM handoff

**Date:** 2026-08-07  
**Script:** `artifacts/stock-scanner-api/gamma_blast_backtest.py`

## Can the current Polygon plan run this pattern?

| Data need | Required for | Plan / evidence |
|-----------|--------------|-----------------|
| SPY 1-min underlying bars | Compression + direction + synthetic mode | **Yes** — already used by Pattern Lab / `zero_dte_bt.py` / many backtests |
| Live options chain snapshot | Live scans (not this backtest) | **Yes** — `aiem_polygon_options_chain.py` uses `/v3/snapshot/options/` |
| Historical 0DTE option 1-min aggregates (`O:SPY…`) | **Real** pricing mode P&L | **Likely yes if you have Options Starter+** — same endpoint family as `zero_dte_bt.py`. Confirm with one probe day on Replit. |
| Grouped daily full-market stocks | Not needed here | Known **403** on current stocks tier (unrelated) |

**Bottom line**

- **Logic / signal backtest (`--mode synthetic`):** your current Stocks minute data is enough. Treat results as a logic check only — not real P&L.
- **Trustworthy options P&L (`--mode real`):** needs Polygon **Options** minute aggregates (Starter or higher). If a probe returns empty/`NOT_AUTHORIZED` for `O:SPY…` 1-min bars, upgrade Options tier or supply a chain CSV.
- **Live broker buys tomorrow:** still **no** — this is backtest-only. Pattern Lab remains paper until a broker hook is built.

## How AIEM is asked (no chat inbox)

On the Replit stock-api host (where `POLYGON_API_KEY` is set):

```bash
cd artifacts/stock-scanner-api
python gamma_blast_backtest.py --days 20 --mode synthetic
# after Options entitlement confirmed:
python gamma_blast_backtest.py --days 20 --mode real
# optional quick TP/SL grid (every variant kept):
python gamma_blast_backtest.py --days 20 --mode synthetic --sweep-quick
```

## Results are kept for variable sweeps

Every run archives **full trade ledger + config** under:

`docs/verification/gamma-blast/`

| File | Purpose |
|------|---------|
| `gamma-blast-<label>-<mode>-<end>-<stamp>.json` | Full run (config + every trade + summary) |
| `LATEST-<mode>.json` | Pointer to newest run |
| `RUN_INDEX.jsonl` | Append-only index for comparing variants |

Do **not** throw these away — Joel wants to change knobs (TP/SL/range/breakout/time-stop) and rank which settings work best. CLI overrides: `--take-profit`, `--stop-loss`, `--range-threshold`, `--breakout-threshold`, `--time-stop`, `--risk-per-trade`, `--label`.
