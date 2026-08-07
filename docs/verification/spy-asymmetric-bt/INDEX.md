# SPY Asymmetric Strategies — saved backtest archive

**Keep this folder.** Joel wants to ask follow-up questions against these results.

## What’s saved

| File / pattern | Contents |
|----------------|----------|
| `RANKING_NOSTOP_TPGRID_2026-08-07.json` | All **138** strategy×TP combos ranked (TP 50/75/100/125/150/200%, **no stop**) |
| `RANKING_RIDE_2026-08-07.json` | All **23** strategies ranked with **no take-profit** (ride to expiry flatten only) |
| `*__tp*.json` | Full trade ledger per strategy×TP (138 files) |
| `*__RIDE.json` | Full trade ledger per strategy, ride-only (23 files) |
| `RUN_INDEX.jsonl` | Append log of ranking writes |
| Engine | `artifacts/stock-scanner-api/spy_asymmetric_bt.py` |

## Shared rules

- Underlying: **SPY**
- Window: ~**2 years** (2024-08-07 → 2026-08-07)
- Entry: weekly **Monday**
- Risk budget: **$500** per package (1-lot skipped if debit > $500)
- Pricing: **real Polygon daily option aggregates**
- Branch / PR: `cursor/spy-asymmetric-strategies-bt-e150` (PR #40)

## Quick winners (as of archive)

- **With TP grid:** Long put butterfly @ **+200% TP** → **+$104,676**
- **Ride (no TP):** Long put butterfly → **+$76,805** (still #1, but lower than best TP)

Long call / put / straddle / strangle / call diagonal often show **0 trades** under the $500 budget (ATM packages too expensive for 1 lot).
