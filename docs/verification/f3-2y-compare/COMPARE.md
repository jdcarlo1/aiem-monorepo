# F3 vs website asym packages — comparison

## Live F3 rules (website / `aim_f3_spy_0dte.py`)
- SPY 0DTE, every session with signal
- PM direction → ORB 09:30–09:44 → breakout with PM
- ATM long call/put · **$200 notional** · **−65% premium stop** · else exit 16:00
- No profit target · real option marks (no synthetic)

## Website asym packages (already have ~2y Polygon BTs)
Monday entry · ≤$500 debit (RR cash-secured credit) · TP of |entry| · no stop · ~3-week Friday expiry

| Package (live TP) | Trades | Win rate | Total P&L | Avg $/trade | Source |
|---|---:|---:|---:|---:|---|
| Long Put Butterfly +200% | 91 | 63.7% | **+$104,676** | +$1,150 | asym TP-grid |
| Long Call Butterfly +100% | 92 | 84.8% | **+$76,186** | +$828 | asym TP-grid |
| Put Ladder Defined +150% | 92 | 52.2% | **+$69,455** | +$755 | asym TP-grid |
| Narrow-Wing Call Butterfly +200% | 94 | 86.2% | **+$260,940** | +$2,776 | catalog TP-grid |
| Bullish Risk Reversal +75% | 94 | 91.5% | **+$202,238** | +$2,151 | catalog TP-grid |

## F3 so far (same live rules, real Polygon 1-min options)

| Window | Trades | WR | P&L (65% stop) | P&L (no stop) |
|---|---:|---:|---:|---:|
| ~3 months (Jun–Aug 2026) | 28 | 25.0% | **+$641** | +$1,333 |
| **2 years (live rules)** | *pending* | | | |

## How to read this (important)
These are **not the same game**:
- F3 = **daily** 0DTE, **$200**/shot, stop-based
- Asym = **weekly** multi-leg, **~$500**/shot, take-profit, no stop

Raw total $ favors asym by design (bigger size + more premium capture + high TPs). Fairer lenses after the 2y F3 finishes:
1. **Win rate**
2. **Avg $ per trade**
3. **Return on risk** = total P&L / (risk_per_trade × trades)
4. **Trades/year** (F3 fires far more often)

## 2y F3 run command (Replit / stock-api host)
```bash
cd artifacts/stock-scanner-api
export PYTHONUNBUFFERED=1
export F3_BAG_RATE_SLEEP=0.35   # raise if Polygon 429s
python3 -u f3_bag_backtest.py --days 730 --stop 0.65
```
Results → `docs/verification/f3-bag-backtest/LATEST.json`
