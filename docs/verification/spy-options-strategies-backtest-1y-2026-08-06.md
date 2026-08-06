# SPY Options Strategies — 1 Year Backtest ($100 risk / trade)

Window: **2025-08-01 → 2026-08-04** (253 sessions). Underlying: Neon `polygon_market_daily` SPY (Polygon-ingested bars). Runner: `tools/spy_options_strategy_backtest.py`. Artifact: `artifacts/backtests/spy_options_strategies_1y.json`.

## Ranking by total P&L

| Rank | Strategy | Trades | Total P&L | Win% | Avg/trade | Max DD | Profit factor |
|-----:|----------|-------:|----------:|-----:|----------:|-------:|--------------:|
| 1 | Double Calendar | 15 | $382.49 | 66.7 | $25.50 | $-118.59 | 2.65 |
| 2 | Calendar Spread | 15 | $284.66 | 60.0 | $18.98 | $-98.04 | 2.42 |
| 3 | Double Diagonal | 18 | $167.58 | 66.7 | $9.31 | $-113.09 | 1.76 |
| 4 | Diagonal Spread | 13 | $124.16 | 92.3 | $9.55 | $-13.10 | 10.48 |
| 5 | Long Straddle | 11 | $83.57 | 45.5 | $7.60 | $-141.43 | 1.41 |
| 6 | Bull Call Debit Spread | 22 | $79.43 | 54.5 | $3.61 | $-303.67 | 1.15 |
| 7 | Bear Put Debit Spread | 20 | $57.88 | 45.0 | $2.89 | $-237.60 | 1.12 |
| 8 | Broken-Wing Butterfly | 22 | $49.42 | 72.7 | $2.25 | $-154.77 | 1.18 |
| 9 | Long Butterfly | 13 | $45.84 | 46.2 | $3.53 | $-88.50 | 1.17 |
| 10 | Iron Condor | 15 | $41.79 | 60.0 | $2.79 | $-63.43 | 1.30 |
| 11 | 0DTE Credit Spreads | 228 | $39.69 | 86.8 | $0.17 | $-385.07 | 1.02 |
| 12 | Reverse Iron Condor | 7 | $22.99 | 57.1 | $3.28 | $-74.30 | 1.14 |
| 13 | Bear Call Credit Spread | 21 | $-0.48 | 61.9 | $-0.02 | $-56.44 | 1.00 |
| 14 | Bull Put Credit Spread | 24 | $-10.71 | 75.0 | $-0.45 | $-89.35 | 0.95 |
| 15 | Iron Butterfly | 12 | $-48.56 | 41.7 | $-4.05 | $-139.18 | 0.79 |
| 16 | Long Strangle | 17 | $-90.57 | 35.3 | $-5.33 | $-277.27 | 0.85 |
| 17 | Call Ratio Spread | 19 | $-113.80 | 57.9 | $-5.99 | $-214.93 | 0.75 |
| 18 | Put Ratio Spread | 23 | $-166.42 | 73.9 | $-7.24 | $-384.40 | 0.74 |

**Most profitable (this window):** Double Calendar (+$382.49 on ~$100 defined risk per entry).

## How these patterns are usually exited

Mechanical rules used in the industry (and in this backtest):

| Family | Take profit | Stop loss | Time stop |
|--------|-------------|-----------|-----------|
| Credit (verticals, IC, iron fly, 0DTE credit, BWB when credit) | Buy back at **50% of credit** | Buy back at **2× credit** | **21 DTE** (0DTE → EOD settlement) |
| Debit verticals | **50% of debit** as profit proxy | Lose **50% of debit** | **21 DTE** |
| Long vol (straddle / strangle / reverse IC) | **+50%** of premium | **−50%** of premium | **14 DTE** |
| Calendar / diagonal / double* | **50% of debit** | Full debit lost | Front month **7 DTE** |
| Ratio (1×2) | **50% of \|premium\|** | **2× \|premium\|** | **21 DTE** |

Sources for the credit 50% / 2× / 21 DTE triad: common systematic credit-spread practice (e.g. OptionsPilot / ApexVol / TradeAlgo style playbooks). Holding credit spreads to expiration is generally worse on risk-adjusted metrics because the last half of max profit carries most of the gamma risk.

Entry conventions here: **~45 DTE**, short strike **~16Δ** for credits; weekly Monday entries (0DTE = every session, fade prior-day return). Position size = fractional contracts so **defined max loss ≈ $100**.

## Data / accuracy caveats (read before trusting $)

1. **Underlying** = Neon `polygon_market_daily` SPY (Polygon-ingested). Live `POLYGON_API_KEY` in this environment returns **Unknown API Key**, so **historical option NBBO was not available**.
2. Option marks are **Black–Scholes** with **IV = 1.10 × 20-day realized vol** (flat smile, no earnings IV crush, no bid/ask).
3. No slippage, commissions, dividends, early assignment, or pin risk.
4. Sample is a **strong SPY uptrend** (621 → 771). That favors calendars/diagonals that benefit from gradual drift + vol contraction vs naked short-vol blowups; it does **not** generalize to chop or crash regimes.
5. Ratio spreads are **undefined risk**; sizing uses the 2×-premium stop as the $100 budget — large adverse gaps can still exceed that in live trading.
6. Tradier live chain spot-check was **401 Unauthorized** in this run — could not cross-validate BS mids vs exchange quotes today.

## Verification checks (all PASS)

- ✅ **put_call_parity_atm**: |C-P - (S-Ke^-rT)| = 0 (tol 0.05)
- ✅ **intrinsic_bounds**: BS call/put ≥ intrinsic
- ✅ **bull_put_short_otm_16delta**: short put below spot near −16Δ (bug fixed: put strike search was inverted and produced deep-ITM shorts)
- ✅ **bull_put_max_loss_identity**: max loss = width − credit
- ✅ **bull_put_max_loss_at_expiry_breach**: expiry P&L under short strike ≈ −max loss
- ✅ **iron_condor_defined_risk**: positive credit, positive defined risk
- ✅ **spy_bars_1y**: 253 bars 2025-08-01 → 2026-08-04

## Reproduce

```bash
source /tmp/cursor-secrets/env.sh   # needs DATABASE_URL
python3 tools/spy_options_strategy_backtest.py
```
