# SPY Premarket Range Breakout (PMRB) vs ORB — 1 Year @ $1000/trade

Window: **2025-08-01 → 2026-08-05** (253 sessions). Notional: **$1000/trade**.

**Verdict:** SPY 2025-08-01→2026-08-05 @ $1000/trade (Yahoo 60m prepost): PMRB 207 trades / $1.53 · ORB 130 trades / $26.64 · ORB_range_2R 130 trades / $13.61. Most profitable: ORB.

## Head-to-head (Yahoo 60m + extended hours)

| Strategy | Trades (1y) | Total P&L | Win% | Avg/trade | Max DD |
|----------|------------:|----------:|-----:|----------:|-------:|
| **PMRB** (Premarket High/Low, long+short) | **207** | $1.53 | 51.2 | $0.01 | $-66.23 |
| PMRB long-only | 124 | $4.98 | 55.6 | $0.04 | $-43.28 |
| **ORB** (terminal: 5% hard + 10% trail) | **130** | $26.64 | 47.7 | $0.20 | $-27.89 |
| ORB range-stop 2R (matched exits) | 130 | $13.61 | 46.2 | $0.10 | $-31.45 |

**Trade counts:** PMRB **207** · ORB **130** in one year.

## Rules

### PMRB (= Premarket Breakout / Premarket High-Low)
- Premarket range **04:00–09:29 ET** → PMH / PML
- Long close > PMH or short close < PML (first signal/day)
- Stop opposite side of range · target **2R** · EOD 15:55

### ORB (your terminal tab)
- Range **09:30–09:59 ET** · long-only close > ORB High after 10:00
- 5% hard stop + 10% trail · EOD 15:55

## 5-minute cross-check (~60 days Yahoo max)

| Strategy | Trades | Total P&L | Win% |
|----------|-------:|----------:|-----:|
| PMRB | 37 | $-24.12 | 48.6 |
| ORB terminal | 28 | $-41.26 | 53.6 |
| ORB range 2R | 28 | $-27.69 | 46.4 |

Winner (60d 5m): **PMRB_long_only**

## Data caveats

- Polygon key **401** here → Yahoo Finance extended hours.
- Full year on **60-minute** bars; 5m only covers ~60 days.
- $1000 = notional per entry, not stop-distance risk.
- On SPY, terminal ORB’s 5%/10% stops rarely trigger → mostly EOD exits.

## Prior Polygon 5m ORB file (partial year)

- SPY trades in window through 2025-10-28: **46**
- At $1000 notional: **$25.99**, WR 56.5%

## Reproduce

```bash
python3 tools/spy_pmrb_vs_orb_backtest.py
```
