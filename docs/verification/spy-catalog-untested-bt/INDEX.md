# SPY Catalog Untested Backtest

Full catalog strategies **not** covered by the prior 23-strategy asymmetric BT.

## Rules (same as before)

- SPY, ~2y, weekly Monday entry
- Risk $500 debit max (credits: 1 package)
- TP grid 50/75/100/125/150/200%, **no stop**
- Real Polygon daily option aggregates (`O:SPY…`)
- Stock+option strategies mark SPY shares at daily close

## Run (Cursor / local — do NOT put API key in git)

```bash
export POLYGON_API_KEY=...   # session env only
export ASYM_BT_CACHE=/tmp/spy_asym_bt_cache
python3 artifacts/stock-scanner-api/spy_catalog_untested_bt.py
```

Smoke: `--max-entries 2 --strategies 'Iron Condor|Jade Lizard'`

## Outputs

- `RANKING_NOSTOP_TPGRID_*.json` — full ranking
- `RANKING_PARTIAL.json` — live checkpoint while running
- `*__tp*.json` — per strategy×TP ledgers
- Abstract catalog names (no fixed legs) listed under `skipped_abstract` in ranking rules
