# SPY Premarket Range Breakout (PMRB) vs ORB — 1 Year @ $1000/trade

Window: **2025-08-01 → 2026-08-05** (253 sessions). **$1000 risk per trade** (sized to stop distance).

**Verdict:** SPY 2025-08-01→2026-08-05 @ $1000 RISK/trade (Yahoo 60m prepost): PMRB 207 trades / $4060.52 · ORB 130 trades / $532.66 · ORB_range_2R 130 trades / $3521.56. Most profitable: PMRB.

## Head-to-head (Yahoo 60m + extended hours)

| Strategy | Trades (1y) | Total P&L | Win% | Avg/trade | Max DD |
|----------|------------:|----------:|-----:|----------:|-------:|
| **PMRB** (Premarket High/Low, long+short) | **207** | $4060.52 | 51.2 | $19.62 | $-6811.13 |
| PMRB long-only | 124 | $1590.15 | 55.6 | $12.82 | $-5367.01 |
| **ORB** (terminal: 5% hard + 10% trail) | **130** | $532.66 | 47.7 | $4.10 | $-557.56 |
| ORB range-stop 2R (matched exits) | 130 | $3521.56 | 46.2 | $27.09 | $-5097.28 |

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
| PMRB | 37 | $87.99 | 48.6 |
| ORB terminal | 28 | $-825.08 | 53.6 |
| ORB range 2R | 28 | $-5158.23 | 46.4 |

Winner (60d 5m): **PMRB**

## Data caveats

- Polygon key **401** here → Yahoo Finance extended hours.
- Full year on **60-minute** bars; 5m only covers ~60 days.
- $1000 = **risk to the stop** per entry (shares = 1000 / |entry−stop|), not $1000 of stock.
- On SPY, terminal ORB’s 5%/10% stops rarely trigger → mostly EOD exits; 5% risk sizing makes dollar P&L small vs range stops.

## Prior Polygon 5m ORB file (partial year)

- SPY trades in window through 2025-10-28: **46**
- At $1000 notional: **$519.85**, WR 56.5%

## Reproduce

```bash
python3 tools/spy_pmrb_vs_orb_backtest.py
```
