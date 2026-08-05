# Pattern Lab Backtest — Last 6 Months

**Period:** 2026-02-05 → 2026-08-05  
**Symbol:** SPY (Polygon 1-min aggregates, 113,812 bars / 124 sessions)  
**Risk:** $100 fixed per trade, no compounding  
**Slippage:** $0.04 round-trip model (half on each side of entry/stop/target)  
**Script:** `artifacts/stock-scanner-api/backtest_pattern_lab.py`

```bash
export POLYGON_API_KEY=...
python3 backtest_pattern_lab.py --symbol SPY --start 2026-02-05 --end 2026-08-05
```

## Baseline ranking (default pattern params)

| Pattern | Trades | Win% | Net P&L | Avg/trade |
|---------|-------:|-----:|--------:|----------:|
| VWAP_REVERSION | 587 | 49.2% | **+$18,918.87** | +$32.23 |
| ORB (15m, 2R) | 431 | 46.9% | +$1,392.59 | +$3.23 |
| WEEKLY_MACRO_ORB (30m, 1.5R) | 390 | 48.5% | +$1,309.19 | +$3.36 |
| HIGH_BETA_ORB (15m, 2R, rvol≥1.5) | 384 | 46.4% | +$956.50 | +$2.49 |
| LIQUIDITY_SWEEP (1.5R) | 367 | 46.6% | +$624.03 | +$1.70 |
| GAP_FILL | 39 | 23.1% | +$357.21 | +$9.16 |

All six were net-positive over this window at $100 risk/trade.

## Stop / target R sweeps (same bars)

### ORB (15m) — target_r

| target_r | Trades | Win% | Net P&L | Avg/trade |
|---------:|-------:|-----:|--------:|----------:|
| 1.0 | 482 | 49.8% | +$473 | +$0.98 |
| 1.5 | 444 | 48.6% | +$971 | +$2.19 |
| 2.0 (default) | 431 | 46.9% | +$1,393 | +$3.23 |
| 2.5 | 416 | 47.1% | +$1,703 | +$4.09 |
| **3.0** | 409 | 46.9% | **+$1,811** | **+$4.43** |

Best ORB money: **3.0R** (higher R beats higher win% here).

### WEEKLY_MACRO_ORB (30m) — target_r

| target_r | Trades | Win% | Net P&L | Avg/trade |
|---------:|-------:|-----:|--------:|----------:|
| 1.0 | 411 | 49.6% | +$591 | +$1.44 |
| 1.5 (default) | 390 | 48.5% | +$1,309 | +$3.36 |
| 2.0 | 375 | 48.0% | +$1,675 | +$4.47 |
| **2.5** | 364 | 47.5% | **+$1,803** | **+$4.95** |
| 3.0 | 362 | 47.8% | +$1,784 | +$4.93 |

Best weekly-macro money: **2.5R**.

### LIQUIDITY_SWEEP — target_r

| target_r | Trades | Win% | Net P&L | Avg/trade |
|---------:|-------:|-----:|--------:|----------:|
| 1.0 | 416 | 55.5% | **−$1,406** | −$3.38 |
| **1.5 (default)** | 367 | 46.6% | **+$624** | **+$1.70** |
| 2.0 | 327 | 37.3% | −$727 | −$2.22 |
| 2.5 | 306 | 32.0% | −$602 | −$1.97 |
| 3.0 | 293 | 28.0% | −$456 | −$1.56 |

Only **1.5R** was profitable in this sweep — keep default.

### VWAP_REVERSION — entry_sd × stop_sd

| entry_sd | stop_sd | Trades | Win% | Net P&L | Avg/trade |
|---------:|--------:|-------:|-----:|--------:|----------:|
| 1.5 | 2.5 | 1115 | 58.5% | +$46,299 | +$41.52 |
| 2.0 | 2.5 | 960 | 60.1% | +$44,948 | +$46.82 |
| 2.5 | 3.0 | 449 | 55.2% | +$23,497 | +$52.33 |
| 1.5 | 3.0 | 697 | 51.9% | +$20,040 | +$28.75 |
| 2.0 | 3.0 (default) | 587 | 49.2% | +$18,919 | +$32.23 |
| 2.5 | 3.5 | 278 | 46.4% | +$12,178 | +$43.81 |
| 2.0 | 3.5 | 388 | 43.6% | +$8,544 | +$22.02 |
| 1.5 | 3.5 | 488 | 48.4% | +$8,020 | +$16.43 |

Tighter stops (stop closer to entry band) dominate P&L because risk-per-share shrinks → more shares at fixed $100 risk. Treat as parameter sensitivity, not live sizing advice until walk-forward / out-of-sample is run.

## Takeaways

1. **VWAP_REVERSION** dominated the 6-month window under default and swept params.
2. ORB family prefers **higher targets (2.5–3R)** over 1R on SPY in this period.
3. **LIQUIDITY_SWEEP** is fragile — only 1.5R worked; 1R win% looked good but lost money.
4. **GAP_FILL** traded rarely (39) with low win% but still slightly green via asymmetric fill target.

Machine-readable twin: `pattern-lab-backtest-6mo.json`.
